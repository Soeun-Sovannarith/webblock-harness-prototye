"""Path sanitisation and protected-file policy for generated source changes."""

from __future__ import annotations

import fnmatch
import posixpath
import re

from webblock_harness.domain import Scope

# Files the harness must never create, modify or delete, regardless of approval.
PROTECTED_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    ".git/**",
    ".github/**",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa*",
    "**/secrets/**",
    "**/*.secret*",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "vercel.json",
    "netlify.toml",
    "Dockerfile",
    "docker-compose*.yml",
    "middleware.ts",
    "middleware.js",
    "src/middleware.ts",
    "src/middleware.js",
    "webblock/**",  # harness-managed (configuration file, harness metadata)
)

# Paths that may only be changed with explicit approval (or a predefined policy).
HIGH_IMPACT_PATTERNS: dict[str, str] = {
    "package.json": "new_dependency",
    "next.config.*": "shared_infrastructure",
    "tsconfig.json": "shared_infrastructure",
    "**/auth/**": "authentication",
    "**/*auth*.ts": "authentication",
    "**/*auth*.tsx": "authentication",
    "**/payment*/**": "payments",
    "**/checkout/**": "payments",
    "**/stripe*": "payments",
    "**/tenant*": "tenant_isolation",
    "src/lib/db/**": "database_schema",
    "drizzle/**": "database_schema",
    "prisma/**": "database_schema",
    "migrations/**": "database_schema",
    "**/schema.ts": "database_schema",
}

# Where each scope is allowed to write.
WRITE_ZONES: dict[Scope, tuple[str, ...]] = {
    Scope.FRONTEND: (
        "src/components/generated/**",
        "src/components/**",
        "src/app/**",
        "src/hooks/**",
        "src/lib/client/**",
        "public/generated/**",
        "src/puck/**",
        "puck.config.tsx",
        "src/puck.config.tsx",
        "tests/**",
        "__tests__/**",
        "src/**/*.test.ts",
        "src/**/*.test.tsx",
    ),
    Scope.BACKEND: (
        "src/app/api/**",
        "src/server/**",
        "src/lib/server/**",
        "tests/**",
        "__tests__/**",
    ),
    Scope.DATABASE: (
        "drizzle/**",
        "prisma/**",
        "migrations/**",
        "src/lib/db/**",
        "src/db/**",
    ),
}

# The area generated components live in; changes outside it are surfaced in the plan.
GENERATED_AREA = ("src/components/generated/**", "public/generated/**")

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-\.\[\]\(\)@+]+$")


class PathPolicyError(ValueError):
    pass


def normalize_repo_path(raw: str) -> str:
    """Return a canonical, repository-relative POSIX path or raise PathPolicyError."""
    if not isinstance(raw, str) or not raw.strip():
        raise PathPolicyError("Empty path.")
    if "\x00" in raw or "\\" in raw:
        raise PathPolicyError(f"Illegal characters in path: {raw!r}")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise PathPolicyError(f"Absolute paths are not allowed: {raw!r}")
    parts = raw.split("/")
    if any(p in ("..", "") for p in parts[:-1]) or parts[-1] in ("..", ".", ""):
        raise PathPolicyError(f"Path traversal or empty segment: {raw!r}")
    norm = posixpath.normpath(raw)
    if norm.startswith("..") or norm != raw.removeprefix("./"):
        raise PathPolicyError(f"Non-canonical path: {raw!r}")
    for seg in norm.split("/"):
        if not _SAFE_SEGMENT.match(seg):
            raise PathPolicyError(f"Unsafe path segment {seg!r} in {raw!r}")
    return norm


def matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatchcase(path, pattern):
            return True
        # Allow "**/" prefixed patterns to match at the repository root as well.
        if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
            return True
    return False


def is_protected(path: str) -> bool:
    return matches(path, PROTECTED_PATTERNS)


def high_impact_reason(path: str) -> str | None:
    for pattern, reason in HIGH_IMPACT_PATTERNS.items():
        if matches(path, (pattern,)):
            return reason
    return None


# Server-side areas that live inside frontend globs (e.g. src/app/**) but require the backend scope.
BACKEND_ONLY = ("src/app/api/**", "src/server/**", "src/lib/server/**")


def allowed_for_scopes(path: str, scopes: set[Scope]) -> bool:
    if matches(path, BACKEND_ONLY) and Scope.BACKEND not in scopes:
        return False
    return any(matches(path, WRITE_ZONES[s]) for s in scopes if s in WRITE_ZONES)


def in_generated_area(path: str) -> bool:
    return matches(path, GENERATED_AREA)


def safe_storage_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", value):
        raise PathPolicyError(f"Unsafe storage key segment: {value!r}")
    return value
