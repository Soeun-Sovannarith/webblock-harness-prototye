"""Validation of Puck site configurations against the trusted component registry."""

from __future__ import annotations

import re
from typing import Any

from webblock_harness.domain import CheckResult, CheckStatus, FieldSpec, ValidationIssue
from webblock_harness.puck.tree import is_component
from webblock_harness.registry.registry import ComponentRegistry

_PAGE_KEY = re.compile(r"^/([a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)*)?$")
_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_CSS_VAR = re.compile(r"^var\(--[a-z0-9-]{1,40}\)$")
_COMPONENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:]{0,127}$")
_SAFE_URL = re.compile(r"^(https?://[^\s<>\"']+|mailto:[^\s<>\"']+|tel:\+?[0-9 ()\-]{3,32}|/(?!/)[^\s<>\"']*|#[A-Za-z0-9_\-]*)$")
_SAFE_IMAGE = re.compile(r"^(https://[^\s<>\"']+|/(?!/)[^\s<>\"']+)$")
MAX_COMPONENTS_PER_PAGE = 500


def _issue(code: str, message: str, location: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, location=location)


def _check_value(
    spec: FieldSpec,
    value: Any,
    loc: str,
    registry: ComponentRegistry,
    out: list[ValidationIssue],
    seen: set[str],
    page: str,
    count: list[int],
) -> None:
    t = spec.type
    if value is None:
        if spec.required:
            out.append(_issue("required", "Required value is null.", loc))
        return
    if value == "" and not spec.required and t in ("url", "image", "color"):
        return  # the visual editor stores a cleared optional field as ""; components treat it as unset
    if t in ("text", "textarea"):
        if not isinstance(value, str):
            out.append(_issue("type", f"Expected text, got {type(value).__name__}.", loc))
            return
        if "\x00" in value:
            out.append(_issue("invalid_text", "Text contains a NUL character.", loc))
        if spec.max_length and len(value) > spec.max_length:
            out.append(_issue("max_length", f"Text exceeds {spec.max_length} characters.", loc))
        if spec.required and not value.strip():
            out.append(_issue("required", "Required text is empty.", loc))
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out.append(_issue("type", f"Expected a number, got {type(value).__name__}.", loc))
            return
        if spec.min is not None and value < spec.min:
            out.append(_issue("min", f"Value {value} is below the minimum {spec.min}.", loc))
        if spec.max is not None and value > spec.max:
            out.append(_issue("max", f"Value {value} is above the maximum {spec.max}.", loc))
    elif t == "boolean":
        if not isinstance(value, bool):
            out.append(_issue("type", "Expected true or false.", loc))
    elif t == "url":
        if not isinstance(value, str) or not _SAFE_URL.match(value.strip()):
            out.append(
                _issue(
                    "unsafe_url", f"URL {str(value)[:80]!r} is not an allowed http(s), mailto, tel, relative or anchor link.", loc
                )
            )
    elif t == "image":
        if not isinstance(value, str) or not _SAFE_IMAGE.match(value.strip()):
            out.append(_issue("unsafe_image", "Images must be https URLs or site-relative paths.", loc))
    elif t == "color":
        if not isinstance(value, str) or not (_HEX_COLOR.match(value) or _CSS_VAR.match(value)):
            out.append(_issue("invalid_color", f"{value!r} is not a hex colour or theme token.", loc))
    elif t == "select":
        if value not in (spec.options or []):
            out.append(_issue("unsupported_value", f"{value!r} is not one of {spec.options}.", loc))
    elif t == "array":
        if not isinstance(value, list):
            out.append(_issue("type", "Expected a list.", loc))
            return
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                out.append(_issue("type", "List items must be objects.", f"{loc}[{i}]"))
                continue
            _check_props(spec.item_fields or {}, item, f"{loc}[{i}]", registry, out, seen, page, count)
    elif t == "object":
        if not isinstance(value, dict):
            out.append(_issue("type", "Expected an object.", loc))
            return
        _check_props(spec.item_fields or {}, value, loc, registry, out, seen, page, count)
    elif t == "slot":
        if not isinstance(value, list):
            out.append(_issue("type", "Slot must be a list of components.", loc))
            return
        _check_components(value, loc, registry, out, seen, page, count)


def _check_props(
    fields: dict[str, FieldSpec],
    props: dict[str, Any],
    loc: str,
    registry: ComponentRegistry,
    out: list[ValidationIssue],
    seen: set[str],
    page: str,
    count: list[int],
) -> None:
    for name in props:
        if name != "id" and name not in fields:
            out.append(_issue("unknown_property", f"Property {name!r} is not declared in the registry schema.", f"{loc}.{name}"))
    for name, spec in fields.items():
        if name not in props:
            if spec.required:
                out.append(_issue("required", f"Missing required property {name!r}.", f"{loc}.{name}"))
            continue
        _check_value(spec, props[name], f"{loc}.{name}", registry, out, seen, page, count)


def _check_components(
    items: list[Any],
    loc: str,
    registry: ComponentRegistry,
    out: list[ValidationIssue],
    seen: set[str],
    page: str,
    count: list[int],
) -> None:
    for i, item in enumerate(items):
        here = f"{loc}[{i}]"
        count[0] += 1
        if not is_component(item):
            out.append(_issue("invalid_component", "Expected {type, props}.", here))
            continue
        ctype = item["type"]
        cid = item["props"].get("id")
        if not isinstance(cid, str) or not _COMPONENT_ID.match(cid):
            out.append(_issue("invalid_id", "Component props.id must be a safe non-empty string.", here))
        elif cid in seen:
            out.append(_issue("duplicate_id", f"Component id {cid!r} is used more than once on the page.", here))
        else:
            seen.add(cid)
        spec = registry.get(ctype)
        if spec is None:
            out.append(_issue("unsupported_component", f"Component type {ctype!r} is not in the registry.", here))
            continue
        if not spec.allowed_in_client_pages:
            out.append(_issue("component_not_allowed", f"Component type {ctype!r} is not permitted in client pages.", here))
        _check_props(spec.fields, item["props"], f"{here}<{ctype}>", registry, out, seen, page, count)


def validate_site(config: dict[str, Any], registry: ComponentRegistry) -> CheckResult:
    issues: list[ValidationIssue] = []
    pages = config.get("pages")
    if not isinstance(pages, dict) or not pages:
        issues.append(_issue("no_pages", "Configuration has no pages.", "pages"))
        return CheckResult(name="puck_schema", status=CheckStatus.FAILED, issues=issues)
    for page_key, page in pages.items():
        loc = f"pages[{page_key!r}]"
        if not _PAGE_KEY.match(page_key):
            issues.append(_issue("invalid_page_key", f"Page key {page_key!r} must look like '/' or '/about-us'.", loc))
        root_props = page.get("root", {}).get("props", {})
        seen: set[str] = set()
        count = [0]
        _check_props(registry.root.fields, root_props, f"{loc}.root", registry, issues, seen, page_key, count)
        _check_components(page.get("content", []), f"{loc}.content", registry, issues, seen, page_key, count)
        for zone_key, items in (page.get("zones") or {}).items():
            parent = zone_key.split(":", 1)[0]
            if ":" not in zone_key or parent not in seen:
                issues.append(
                    _issue("dangling_zone", f"Zone {zone_key!r} does not belong to a component on this page.", f"{loc}.zones")
                )
            if isinstance(items, list):
                _check_components(items, f"{loc}.zones[{zone_key!r}]", registry, issues, seen, page_key, count)
        if count[0] > MAX_COMPONENTS_PER_PAGE:
            issues.append(_issue("too_many_components", f"Page has more than {MAX_COMPONENTS_PER_PAGE} components.", loc))
    return CheckResult(
        name="puck_schema",
        status=CheckStatus.FAILED if issues else CheckStatus.PASSED,
        issues=issues,
        details={"pages": len(pages)},
    )
