"""Static validation of AI-generated source patches (Path B).

Nothing generated is executed here. These checks run before a patch is ever
committed, and a failure stops the job. Build, type-check, lint and tests run
afterwards in the client repository's CI (an isolated runner without WebBlock
credentials) on the job branch — never on the harness host.
"""

from __future__ import annotations

import difflib
import fnmatch
import re
from dataclasses import dataclass, field

from webblock_harness.analyzer.classifier import approvals_for_source_plan
from webblock_harness.config import Settings
from webblock_harness.domain import (
    ApprovalReason,
    CheckResult,
    CheckStatus,
    CodeChangeProposal,
    FileAction,
    PlannedFile,
    Scope,
    SourcePlan,
    ValidationIssue,
    ValidationReport,
)
from webblock_harness.integrations.git import FileBlob
from webblock_harness.registry.registry import ComponentRegistry, validate_component_spec
from webblock_harness.security.paths import (
    PathPolicyError,
    allowed_for_scopes,
    in_generated_area,
    is_protected,
    normalize_repo_path,
)

_CODE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

_FORBIDDEN: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\beval\s*\("), "eval", "eval() is not allowed in generated code."),
    (re.compile(r"\bnew\s+Function\s*\("), "new_function", "new Function() is not allowed."),
    (
        re.compile(
            r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*|\bimport\s+)['"](?:node:)?(child_process|vm|worker_threads|cluster)['"]"""
        ),
        "dangerous_module",
        "Process/VM modules are not allowed.",
    ),
    (re.compile(r"dangerouslySetInnerHTML"), "raw_html", "dangerouslySetInnerHTML is not allowed."),
    (re.compile(r"\brequire\s*\(\s*[^'\"\s)]"), "dynamic_require", "Dynamic require() is not allowed."),
    (re.compile(r"\bimport\s*\(\s*[^'\"\s)]"), "dynamic_import", "Dynamic import() with a computed specifier is not allowed."),
    (
        re.compile(
            r"(?i)https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|169\.254\.169\.254|metadata\.google\.internal|[a-z0-9.-]+\.internal)\b"
        ),
        "internal_host",
        "Generated code may not call internal infrastructure.",
    ),
    (re.compile(r"\bdocument\.cookie\b"), "cookie_access", "Direct cookie access is not allowed."),
    (
        re.compile(r"\blocalStorage\.(getItem|setItem)\(\s*['\"][^'\"]*(token|secret|password)"),
        "token_storage",
        "Storing credentials in localStorage is not allowed.",
    ),
]
_SECRETS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{10,}"),
    re.compile(r"sk_(live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|secret|api[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]
_ENV = re.compile(r"process\.env(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]|\[)")
_SQL_INTERPOLATION = re.compile(r"(?is)`\s*(select|insert|update|delete)\b[^`]*\$\{")
_DESTRUCTIVE_SQL = re.compile(
    r"(?i)\b(drop\s+(table|column|schema|index|database)|truncate\b|delete\s+from|alter\s+table\s+\S+\s+(drop|alter\s+column\s+\S+\s+type)|rename\s+(to|column))"
)
_EXPORT = "export\\s+(default\\s+)?(function|const|class)\\s+{name}\\b|export\\s*\\{{[^}}]*\\b{name}\\b"


@dataclass
class PatchReview:
    report: ValidationReport
    diff: str = ""
    stats: dict[str, int] = field(default_factory=dict)
    approval_reasons: set[ApprovalReason] = field(default_factory=set)


def _issue(code: str, message: str, location: str | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, location=location)


def _check(name: str, issues: list[ValidationIssue], **details: object) -> CheckResult:
    return CheckResult(
        name=name,
        status=CheckStatus.FAILED if any(i.severity == "error" for i in issues) else CheckStatus.PASSED,
        issues=issues,
        details=dict(details),
    )


def _env_allowed(var: str, allowlist: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(var, pattern) for pattern in allowlist)


def validate_patch(
    proposal: CodeChangeProposal,
    plan: SourcePlan,
    base_files: dict[str, FileBlob | None],
    registry: ComponentRegistry,
    settings: Settings,
    approved: set[ApprovalReason],
    user_authored: set[str],
) -> PatchReview:
    review = PatchReview(report=ValidationReport())
    report = review.report
    scopes = {Scope(s) for s in proposal.scopes} | {Scope.FRONTEND}
    plan_scopes = {Scope(s) for s in plan.scopes} | {Scope.FRONTEND}

    # 1. Path policy & protected files ------------------------------------
    path_issues: list[ValidationIssue] = []
    protected_issues: list[ValidationIssue] = []
    normalized: dict[str, str] = {}
    for f in proposal.files:
        try:
            p = normalize_repo_path(f.path)
        except PathPolicyError as exc:
            path_issues.append(_issue("invalid_path", str(exc), f.path))
            continue
        if p in normalized.values():
            path_issues.append(_issue("duplicate_path", "The same file appears twice in the patch.", p))
        normalized[f.path] = p
        if is_protected(p):
            protected_issues.append(_issue("protected_file", "This file is protected and cannot be changed by the harness.", p))
        elif not allowed_for_scopes(p, scopes):
            path_issues.append(
                _issue("outside_write_zone", f"Path is outside the write zones for scopes {sorted(s.value for s in scopes)}.", p)
            )
        if f.action != FileAction.DELETE and f.content is None:
            path_issues.append(_issue("missing_content", "Created/modified files need full content.", p))
        if f.content is not None and len(f.content.encode()) > settings.max_file_bytes:
            path_issues.append(_issue("file_too_large", f"File exceeds {settings.max_file_bytes} bytes.", p))
    if len(proposal.files) > settings.max_patch_files:
        path_issues.append(
            _issue("too_many_files", f"Patch touches {len(proposal.files)} files (limit {settings.max_patch_files}).")
        )
    if not proposal.files:
        path_issues.append(_issue("empty_patch", "The generated patch contains no file changes."))
    report.add(_check("path_policy", path_issues))
    report.add(_check("protected_files", protected_issues))

    # 2. Plan conformance ---------------------------------------------------
    conf: list[ValidationIssue] = []
    planned = {normalize_or_raw(pf.path): pf for pf in plan.files_to_change}
    for f in proposal.files:
        p = normalized.get(f.path, f.path)
        pf = planned.get(p)
        if pf is None and not _is_test_file(p):
            conf.append(_issue("unplanned_file", "File is not part of the approved plan.", p))
        elif pf is not None and pf.action != f.action.value:
            conf.append(_issue("unplanned_action", f"Plan said {pf.action}, patch does {f.action.value}.", p))
    if not scopes <= plan_scopes:
        conf.append(
            _issue(
                "scope_escalation",
                f"Patch scopes {sorted(s.value for s in scopes)} exceed the plan {sorted(s.value for s in plan_scopes)}.",
            )
        )
    planned_deps = {d.name for d in plan.dependencies}
    for d in proposal.dependencies:
        if d.name not in planned_deps:
            conf.append(_issue("unplanned_dependency", f"Dependency {d.name} was not in the plan.", "package.json"))
    planned_migrations = {normalize_or_raw(pm.path) for pm in plan.migrations}
    for mig in proposal.migrations:
        if normalize_or_raw(mig.path) not in planned_migrations:
            conf.append(_issue("unplanned_migration", "Migration was not in the plan.", mig.path))
    report.add(_check("plan_conformance", conf))

    # 3. Conflict detection against the base revision ----------------------
    conflict: list[ValidationIssue] = []
    for f in proposal.files:
        cp = normalized.get(f.path)
        if cp is None:
            continue
        base = base_files.get(cp)
        if f.action == FileAction.CREATE and base is not None:
            conflict.append(_issue("file_exists", "Patch creates a file that already exists; it would overwrite it.", cp))
        if f.action in (FileAction.MODIFY, FileAction.DELETE):
            if base is None:
                conflict.append(_issue("file_missing", "Patch modifies a file that does not exist at the base revision.", cp))
            elif f.base_sha and f.base_sha != base.sha:
                conflict.append(_issue("stale_base", "File changed since the patch was generated.", cp))
    report.add(_check("base_revision", conflict))

    # 4. Static security scan ------------------------------------------------
    sec: list[ValidationIssue] = []
    for f in proposal.files:
        sp = normalized.get(f.path)
        if sp is None or f.content is None:
            continue
        for rx in _SECRETS:
            if rx.search(f.content):
                sec.append(_issue("hardcoded_secret", "Content looks like a credential.", sp))
                break
        if sp.endswith(_CODE_EXT):
            for rx, code, message in _FORBIDDEN:
                if rx.search(f.content):
                    sec.append(_issue(code, message, sp))
            for em in _ENV.finditer(f.content):
                var = em.group(1) or em.group(2)
                if var is None:
                    sec.append(_issue("dynamic_env", "Computed process.env access is not allowed.", sp))
                elif not _env_allowed(var, settings.generated_code_env_allowlist):
                    sec.append(_issue("env_not_allowed", f"process.env.{var} is not in GENERATED_CODE_ENV_ALLOWLIST.", sp))
                elif not var.startswith("NEXT_PUBLIC_") and not sp.startswith(
                    ("src/app/api/", "src/server/", "src/lib/server/", "src/lib/db/", "src/db/")
                ):
                    sec.append(_issue("server_env_in_client", f"{var} may only be read in server code.", sp))
            if _SQL_INTERPOLATION.search(f.content) and "sql`" not in f.content:
                sec.append(_issue("sql_interpolation", "Raw SQL built with string interpolation; use parameterised queries.", sp))
            if (
                re.match(r"^src/app/api/.+/route\.(ts|js)$", sp)
                and re.search(r"export\s+(async\s+)?function\s+(POST|PUT|PATCH|DELETE)\b", f.content)
                and "zod" not in f.content
            ):
                sec.append(_issue("unvalidated_input", "Mutating API routes must validate input with zod.", sp))
        is_migration = sp.endswith(".sql") or "/migrations/" in f"/{sp}" or sp.startswith(("drizzle/", "prisma/"))
        if is_migration and _DESTRUCTIVE_SQL.search(f.content) and ApprovalReason.DESTRUCTIVE_MIGRATION not in approved:
            sec.append(_issue("destructive_migration", "Migration contains destructive SQL and was not approved.", sp))
    report.add(_check("static_security_scan", sec))

    # 5. Component registry schema ------------------------------------------
    comp: list[ValidationIssue] = []
    patch_paths = {normalized.get(f.path): f for f in proposal.files}
    for nc in proposal.new_components:
        for problem in validate_component_spec(nc.spec):
            comp.append(_issue("invalid_component_spec", problem, nc.spec.name))
        if registry.get(nc.spec.name) is not None:
            comp.append(
                _issue("component_exists", f"{nc.spec.name} already exists in the registry; extend it instead.", nc.spec.name)
            )
        try:
            src = normalize_repo_path(nc.source_file)
        except PathPolicyError as exc:
            comp.append(_issue("invalid_path", str(exc), nc.source_file))
            continue
        if not in_generated_area(src):
            comp.append(_issue("component_location", "New components must live in src/components/generated/.", src))
        src_change = patch_paths.get(src)
        if src_change is None or src_change.content is None:
            comp.append(_issue("component_source_missing", "The component source file is not part of the patch.", src))
        elif not re.search(_EXPORT.format(name=re.escape(nc.spec.name)), src_change.content):
            comp.append(_issue("component_export_missing", f"{src} does not export {nc.spec.name}.", src))
    report.add(_check("component_registry_schema", comp))

    # 6. Diff review --------------------------------------------------------
    diffs: list[str] = []
    added = removed = 0
    review_issues: list[ValidationIssue] = []
    for f in proposal.files:
        dp = normalized.get(f.path)
        if dp is None:
            continue
        base_blob = base_files.get(dp)
        old = base_blob.content if base_blob else ""
        new = "" if f.action == FileAction.DELETE else (f.content or "")
        lines = list(
            difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f"a/{dp}", tofile=f"b/{dp}"
            )
        )
        diffs.append("".join(lines))
        file_added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
        file_removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
        added += file_added
        removed += file_removed
        old_lines = max(len(old.splitlines()), 1)
        if f.action == FileAction.MODIFY and file_removed > max(20, old_lines // 2):
            review.approval_reasons.add(ApprovalReason.LARGE_REMOVAL)
            if ApprovalReason.LARGE_REMOVAL not in approved:
                review_issues.append(
                    _issue(
                        "large_rewrite",
                        f"Patch rewrites {file_removed}/{old_lines} lines of an existing file; minimal patches only.",
                        dp,
                    )
                )
        if f.action == FileAction.MODIFY and old == new:
            review_issues.append(_issue("noop_file", "File is listed as modified but its content is unchanged.", dp))
    if added + removed > settings.max_patch_changed_lines:
        review_issues.append(
            _issue("patch_too_large", f"{added + removed} changed lines exceeds {settings.max_patch_changed_lines}.")
        )
    review.diff = "\n".join(diffs)
    review.stats = {"files": len(proposal.files), "lines_added": added, "lines_removed": removed}

    # Approvals implied by what the patch actually does (not just the plan) --
    as_plan = SourcePlan(
        summary=proposal.summary,
        scopes=list(scopes),
        files_to_change=[PlannedFile(path=f.path, action=f.action.value) for f in proposal.files],
        migrations=proposal.migrations,
        dependencies=proposal.dependencies,
    )
    review.approval_reasons |= approvals_for_source_plan(as_plan, user_authored)
    if Scope.DATABASE in scopes:
        review.approval_reasons.add(ApprovalReason.DATABASE_SCHEMA)
    missing = sorted(r.value for r in review.approval_reasons - approved)
    if missing:
        review_issues.append(_issue("approval_required", f"Patch requires approval not granted for: {', '.join(missing)}."))
    report.add(_check("diff_review", review_issues, **review.stats))
    return review


def normalize_or_raw(path: str) -> str:
    try:
        return normalize_repo_path(path)
    except PathPolicyError:
        return path


def _is_test_file(path: str) -> bool:
    return bool(re.search(r"(^|/)(__tests__|tests)/|\.test\.(ts|tsx|js)$|\.spec\.(ts|tsx|js)$", path))
