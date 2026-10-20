"""Parsing and canonicalisation of Puck data.

Two configurations that differ only in key order, whitespace or integral float
formatting (``15`` vs ``15.0``) normalise to the same canonical form and hash.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


class ConfigFormatError(ValueError):
    pass


MAX_CONFIG_BYTES = 2_000_000
MAX_DEPTH = 40


def _normalize_value(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise ConfigFormatError("Configuration is nested too deeply.")
    if isinstance(value, dict):
        return {str(k): _normalize_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(v, depth + 1) for v in value]
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    return value


def normalize_page(page: Any) -> dict[str, Any]:
    if not isinstance(page, dict):
        raise ConfigFormatError("Each page must be a JSON object.")
    page = _normalize_value(page)
    root = page.get("root") or {}
    if not isinstance(root, dict):
        raise ConfigFormatError("root must be an object.")
    # Older Puck versions stored root props directly on root.
    if "props" not in root:
        root = {"props": dict(root)}
    content = page.get("content") or []
    zones = page.get("zones") or {}
    if not isinstance(content, list) or not isinstance(zones, dict):
        raise ConfigFormatError("content must be a list and zones an object.")
    out: dict[str, Any] = {"root": {"props": root.get("props") or {}}, "content": content}
    if zones:
        out["zones"] = zones
    return out


def normalize_site(config: Any) -> dict[str, Any]:
    """Accept a site config ({"pages": {...}}) or a single Puck page (treated as "/")."""
    if isinstance(config, (str, bytes)):
        if len(config) > MAX_CONFIG_BYTES:
            raise ConfigFormatError("Configuration exceeds the size limit.")
        try:
            config = json.loads(config)
        except json.JSONDecodeError as exc:
            raise ConfigFormatError(f"Invalid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigFormatError("Configuration must be a JSON object.")
    if "pages" not in config and ("content" in config or "root" in config):
        config = {"pages": {"/": config}}
    pages = config.get("pages")
    if not isinstance(pages, dict):
        raise ConfigFormatError("Configuration must contain a 'pages' object.")
    normalized = {"pages": {str(k): normalize_page(v) for k, v in pages.items()}}
    if len(canonical_json(normalized)) > MAX_CONFIG_BYTES:
        raise ConfigFormatError("Configuration exceeds the size limit.")
    return normalized


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def empty_site() -> dict[str, Any]:
    return {"pages": {"/": {"root": {"props": {"title": "Home"}}, "content": []}}}


def deep_copy(config: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(config)
