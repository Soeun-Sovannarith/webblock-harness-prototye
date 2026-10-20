"""Targeted, deterministic JSON patch operations for Puck site configurations.

Operations address components by their stable Puck ``props.id``, never by
position, so unrelated content is preserved and concurrent edits elsewhere on the
page do not invalidate a patch. ``expect`` preconditions turn a stale patch into
an explicit conflict instead of a silent overwrite.
"""

from __future__ import annotations

from typing import Any

from webblock_harness.domain import (
    AddComponentOp,
    AddPageOp,
    MoveComponentOp,
    PuckOp,
    RemoveComponentOp,
    RemovePageOp,
    SetPropOp,
    SetRootPropOp,
)
from webblock_harness.puck.normalize import deep_copy
from webblock_harness.puck.tree import get_container, index_components, is_component
from webblock_harness.registry.registry import ComponentRegistry

_MISSING = object()


class OpConflict(Exception):
    def __init__(self, code: str, message: str, op_index: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.op_index = op_index


def _page(config: dict[str, Any], page: str) -> dict[str, Any]:
    pages = config["pages"]
    if page not in pages:
        raise OpConflict("page_not_found", f"Page {page!r} does not exist.")
    return pages[page]  # type: ignore[no-any-return]


def _get_path(obj: Any, path: list[str | int]) -> Any:
    cur = obj
    for seg in path:
        if isinstance(seg, int) and isinstance(cur, list):
            if not -len(cur) <= seg < len(cur):
                return _MISSING
            cur = cur[seg]
        elif isinstance(seg, str) and isinstance(cur, dict):
            if seg not in cur:
                return _MISSING
            cur = cur[seg]
        else:
            return _MISSING
    return cur


def _set_path(obj: Any, path: list[str | int], value: Any) -> None:
    cur = obj
    for seg in path[:-1]:
        nxt = _get_path(cur, [seg])
        if nxt is _MISSING:
            if isinstance(seg, str) and isinstance(cur, dict):
                cur[seg] = {}
                nxt = cur[seg]
            else:
                raise OpConflict("path_not_found", f"Path segment {seg!r} does not exist.")
        cur = nxt
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(cur, list) or not 0 <= last <= len(cur):
            raise OpConflict("path_not_found", f"Index {last} is out of range.")
        if last == len(cur):
            cur.append(value)
        else:
            cur[last] = value
    elif isinstance(cur, dict):
        cur[last] = value
    else:
        raise OpConflict("path_not_found", f"Cannot set {last!r} on a non-object.")


def _check_expect(current: Any, op: SetPropOp | SetRootPropOp) -> None:
    if not op.has_expect:
        return
    observed = None if current is _MISSING else current
    if observed != op.expect:
        raise OpConflict(
            "stale_value",
            f"Expected {op.expect!r} at {op.path} but found {observed!r}; the page changed since the plan was made.",
        )


def _find(config: dict[str, Any], page: str, component_id: str, registry: ComponentRegistry | None):  # type: ignore[no-untyped-def]
    located = index_components(page, _page(config, page), registry).get(component_id)
    if located is None:
        raise OpConflict("component_not_found", f"Component {component_id!r} not found on page {page!r}.")
    return located


def _anchor_index(items: list[Any], before_id: str | None, after_id: str | None, index: int | None) -> int:
    anchors = [a for a in (before_id, after_id, index) if a is not None]
    if len(anchors) > 1:
        raise OpConflict("ambiguous_anchor", "Specify only one of before_id, after_id or index.")
    ids = [c["props"].get("id") if is_component(c) else None for c in items]
    if before_id is not None:
        if before_id not in ids:
            raise OpConflict("anchor_not_found", f"Anchor {before_id!r} is not in the target container.")
        return ids.index(before_id)
    if after_id is not None:
        if after_id not in ids:
            raise OpConflict("anchor_not_found", f"Anchor {after_id!r} is not in the target container.")
        return ids.index(after_id) + 1
    if index is not None:
        if not 0 <= index <= len(items):
            raise OpConflict("index_out_of_range", f"Index {index} is out of range.")
        return index
    return len(items)


def apply_op(config: dict[str, Any], op: PuckOp, registry: ComponentRegistry | None = None) -> None:
    """Apply one operation in place."""
    if isinstance(op, SetPropOp):
        located = _find(config, op.page, op.component_id, registry)
        props = located.component["props"]
        if op.path[0] == "id":
            raise OpConflict("immutable_prop", "Component ids cannot be changed.")
        _check_expect(_get_path(props, op.path), op)
        _set_path(props, op.path, op.value)
    elif isinstance(op, SetRootPropOp):
        root_props = _page(config, op.page).setdefault("root", {}).setdefault("props", {})
        _check_expect(_get_path(root_props, op.path), op)
        _set_path(root_props, op.path, op.value)
    elif isinstance(op, RemoveComponentOp):
        located = _find(config, op.page, op.component_id, registry)
        if op.expect_type and located.type != op.expect_type:
            raise OpConflict("stale_value", f"Component {op.component_id!r} is a {located.type}, not {op.expect_type}.")
        items = get_container(_page(config, op.page), located.container, registry)
        assert items is not None
        items.pop(located.index)
        # Drop legacy zones owned by the removed component.
        zones = _page(config, op.page).get("zones") or {}
        for key in [k for k in zones if k.split(":", 1)[0] == op.component_id]:
            del zones[key]
    elif isinstance(op, MoveComponentOp):
        if op.component_id in (op.before_id, op.after_id):
            raise OpConflict("invalid_anchor", "A component cannot be anchored to itself.")
        page = _page(config, op.page)
        located = _find(config, op.page, op.component_id, registry)
        source = get_container(page, located.container, registry)
        assert source is not None
        component = source.pop(located.index)
        target_key = op.to_container
        if target_key is None:
            anchor_id = op.before_id or op.after_id
            target_key = (
                index_components(op.page, page, registry)[anchor_id].container
                if anchor_id and anchor_id in index_components(op.page, page, registry)
                else located.container
            )
        if target_key.startswith(f"slot:{op.component_id}:") or target_key.startswith(f"zone:{op.component_id}:"):
            raise OpConflict("invalid_move", "A component cannot be moved inside itself.")
        target = get_container(page, target_key, registry)
        if target is None:
            raise OpConflict("container_not_found", f"Container {target_key!r} does not exist.")
        target.insert(_anchor_index(target, op.before_id, op.after_id, op.to_index), component)
    elif isinstance(op, AddComponentOp):
        component = op.component
        if not is_component(component) or not isinstance(component["props"].get("id"), str):
            raise OpConflict("invalid_component", "New components need a type and a string props.id.")
        page = _page(config, op.page)
        if component["props"]["id"] in index_components(op.page, page, registry):
            raise OpConflict("duplicate_id", f"Component id {component['props']['id']!r} already exists.")
        target = get_container(page, op.container, registry)
        if target is None:
            raise OpConflict("container_not_found", f"Container {op.container!r} does not exist.")
        target.insert(_anchor_index(target, op.before_id, op.after_id, op.index), deep_copy(component))
    elif isinstance(op, AddPageOp):
        if op.page in config["pages"]:
            raise OpConflict("page_exists", f"Page {op.page!r} already exists.")
        from webblock_harness.puck.normalize import normalize_page

        config["pages"][op.page] = normalize_page(deep_copy(op.data))
    elif isinstance(op, RemovePageOp):
        _page(config, op.page)
        del config["pages"][op.page]
    else:  # pragma: no cover - exhaustive
        raise OpConflict("unknown_op", f"Unsupported operation {op!r}")


def apply_ops(config: dict[str, Any], ops: list[PuckOp], registry: ComponentRegistry | None = None) -> dict[str, Any]:
    """Return a new configuration with ``ops`` applied atomically (all or nothing)."""
    result = deep_copy(config)
    for i, op in enumerate(ops):
        try:
            apply_op(result, op, registry)
        except OpConflict as exc:
            exc.op_index = i
            raise
    return result
