"""The standalone Next.js + Puck + Drizzle project pushed to a client's repository on first deploy."""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from webblock_harness.security.portal_tokens import ISSUER, audience

SITE_DIR = Path(__file__).with_name("site")
_SKIP_DIRS = {"node_modules", ".next", "out"}
_RENAMES = {"_gitignore": ".gitignore", "_env.example": ".env.example"}
GENERATED_FILES = ("webblock/site.json", "webblock/custom-fields.json", "webblock/seed.json")


def config_file_content(config: dict[str, Any]) -> str:
    """Deterministic, line-oriented JSON so a content edit shows up as a minimal git diff."""
    return json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def render_scaffold(site_name: str, repo_name: str) -> dict[str, str]:
    """Static project files (code). Per-site data files are added by ``render_site``."""
    files: dict[str, str] = {}
    for path in sorted(SITE_DIR.rglob("*")):
        rel = path.relative_to(SITE_DIR)
        if path.is_dir() or _SKIP_DIRS & set(rel.parts) or path.name.endswith(".tsbuildinfo"):
            continue
        name = "/".join((*rel.parts[:-1], _RENAMES.get(rel.name, rel.name)))
        content = path.read_text(encoding="utf-8")
        files[name] = content.replace("__SITE_NAME__", site_name).replace("__REPO_NAME__", repo_name)
    return files


@cache
def site_code_version() -> str:
    """Fingerprint of the site code shipped by this harness version; a deployed site with another value is outdated."""
    digest = hashlib.sha256()
    for name, content in sorted(render_scaffold("", "").items()):
        digest.update(name.encode() + b"\0" + content.encode() + b"\0")
    return digest.hexdigest()[:16]


def render_site(
    *,
    site_name: str,
    repo_name: str,
    tenant_id: str,
    portal_public_key: str,
    custom_fields: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    seed: dict[str, Any],
    config: dict[str, Any],
    config_path: str,
) -> dict[str, str]:
    """Everything pushed on first deploy: code + site identity + business-data seed + page content."""
    files = render_scaffold(site_name, repo_name)
    files["webblock/site.json"] = _json(
        {
            "tenantId": tenant_id,
            "siteName": site_name,
            # Public key only: the site can verify Business Portal sessions but never create them.
            "portal": {"issuer": ISSUER, "audience": audience(tenant_id), "publicKey": portal_public_key},
        }
    )
    files["webblock/custom-fields.json"] = _json(list(custom_fields))
    files["webblock/seed.json"] = _json(seed)
    files[config_path] = config_file_content(config)
    return files
