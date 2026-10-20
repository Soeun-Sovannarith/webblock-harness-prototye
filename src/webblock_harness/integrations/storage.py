"""Artifact storage for large payloads (configurations, proposals, diffs, manifests).

Keys are always tenant-prefixed and built from validated segments, so one tenant
can never address another tenant's artifacts and no key can escape the root.
Content is written once (content-addressed by SHA-256) and never mutated.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from webblock_harness.puck.normalize import canonical_json
from webblock_harness.security.paths import safe_storage_segment


class ArtifactStore(Protocol):
    def put_json(self, tenant_id: str, project_id: str, kind: str, value: Any) -> tuple[str, str]: ...
    def put_text(self, tenant_id: str, project_id: str, kind: str, text: str, ext: str = "txt") -> tuple[str, str]: ...
    def get_json(self, tenant_id: str, key: str) -> Any: ...
    def get_text(self, tenant_id: str, key: str) -> str: ...


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, tenant_id: str, project_id: str, kind: str, digest: str, ext: str) -> str:
        return "/".join(
            [
                "tenants",
                safe_storage_segment(tenant_id),
                "projects",
                safe_storage_segment(project_id),
                safe_storage_segment(kind),
                f"{digest}.{safe_storage_segment(ext)}",
            ]
        )

    def _path(self, tenant_id: str, key: str) -> Path:
        prefix = f"tenants/{safe_storage_segment(tenant_id)}/"
        if not key.startswith(prefix) or any(seg in ("", ".", "..") for seg in key.split("/")):
            raise PermissionError("Artifact belongs to a different tenant.")
        tenant_root = (self.root / "tenants" / tenant_id).resolve()
        path = (self.root / key).resolve()
        if not path.is_relative_to(tenant_root):
            raise PermissionError("Artifact key escapes the tenant's storage area.")
        return path

    def _write(self, tenant_id: str, key: str, data: bytes) -> None:
        path = self._path(tenant_id, key)
        if path.exists():
            return  # content-addressed: identical content already stored
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def put_json(self, tenant_id: str, project_id: str, kind: str, value: Any) -> tuple[str, str]:
        data = canonical_json(value).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        key = self._key(tenant_id, project_id, kind, digest, "json")
        self._write(tenant_id, key, data)
        return key, digest

    def put_text(self, tenant_id: str, project_id: str, kind: str, text: str, ext: str = "txt") -> tuple[str, str]:
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        key = self._key(tenant_id, project_id, kind, digest, ext)
        self._write(tenant_id, key, data)
        return key, digest

    def get_json(self, tenant_id: str, key: str) -> Any:
        return json.loads(self._path(tenant_id, key).read_text(encoding="utf-8"))

    def get_text(self, tenant_id: str, key: str) -> str:
        return self._path(tenant_id, key).read_text(encoding="utf-8")
