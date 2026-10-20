"""Structural (semantic) diff of two Puck site configurations.

The diff is computed over parsed, normalised data and keyed by component id –
never over text – so key order and formatting are irrelevant, and a moved
component is reported as a reorder rather than a delete + insert. The diff can be
turned back into targeted operations (``diff_to_ops``) so that an editor
submission is applied as a localised patch instead of a whole-page replace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from webblock_harness.domain import (
    AddComponentOp,
    AddPageOp,
    ChangeItem,
    ChangeKind,
    FieldSpec,
    MoveComponentOp,
    PuckOp,
    RemoveComponentOp,
    RemovePageOp,
    SetPropOp,
    SetRootPropOp,
)
from webblock_harness.puck.tree import Located, container_ids, index_components, iter_containers
from webblock_harness.registry.registry import ComponentRegistry

_CATEGORY_KIND = {
    "content": ChangeKind.CONTENT_UPDATE,
    "style": ChangeKind.STYLE_UPDATE,
    "layout": ChangeKind.LAYOUT_UPDATE,
    "behavior": ChangeKind.CONTENT_UPDATE,
}


@dataclass
class ConfigDiff:
    changes: list[ChangeItem] = field(default_factory=list)
    ops: list[PuckOp] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes


def _lcs(a: list[str], b: list[str]) -> set[str]:
    """Ids that keep their relative order between ``a`` and ``b``."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    keep: set[str] = set()
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            keep.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return keep


def _leaf_changes(before: Any, after: Any, path: list[str | int]) -> list[tuple[list[str | int], Any, Any, bool]]:
    """Recursive value diff. Returns (path, before, after, existed_before)."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[tuple[list[str | int], Any, Any, bool]] = []
        for key in sorted(set(before) | set(after)):
            if key not in after:
                out.append(([*path, key], before[key], None, True))
            elif key not in before:
                out.append(([*path, key], None, after[key], False))
            else:
                out += _leaf_changes(before[key], after[key], [*path, key])
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            out += _leaf_changes(b, a, [*path, i])
        return out
    return [(path, before, after, True)]


def _field_for_path(fields: dict[str, FieldSpec], path: list[str | int]) -> FieldSpec | None:
    spec: FieldSpec | None = fields.get(str(path[0]))
    rest = path[1:]
    while spec is not None and rest:
        seg = rest[0]
        if isinstance(seg, int):
            rest = rest[1:]
            continue
        spec = (spec.item_fields or {}).get(seg)
        rest = rest[1:]
    return spec


def _classify_prop(registry: ComponentRegistry, comp_type: str, path: list[str | int]) -> tuple[ChangeKind, str]:
    spec = registry.get(comp_type)
    if spec is None:
        return ChangeKind.UNSUPPORTED_COMPONENT, f"{comp_type} is not in the component registry"
    top = spec.fields.get(str(path[0]))
    if top is None:
        return ChangeKind.INVALID_PROPERTY, f"{comp_type} has no property {path[0]!r}"
    leaf = _field_for_path(spec.fields, path)
    if leaf is None:
        return ChangeKind.INVALID_PROPERTY, f"{comp_type}.{'.'.join(map(str, path))} is not a declared property"
    return _CATEGORY_KIND[leaf.category], ""


def _slot_names(registry: ComponentRegistry, comp_type: str) -> set[str]:
    spec = registry.get(comp_type)
    return {k for k, f in spec.fields.items() if f.type == "slot"} if spec else set()


def _set_prop(page: str, cid: str, path: list[str | int], before: Any, after: Any, existed: bool) -> SetPropOp:
    return SetPropOp(page=page, component_id=cid, path=path, value=after, expect=before if existed else None, has_expect=True)


def diff_pages(
    page_key: str,
    base: dict[str, Any],
    proposed: dict[str, Any],
    registry: ComponentRegistry,
    moved_hint: frozenset[str] = frozenset(),
) -> ConfigDiff:
    result = ConfigDiff()

    # Root props --------------------------------------------------------
    root_b = base.get("root", {}).get("props", {})
    root_a = proposed.get("root", {}).get("props", {})
    for path, b, a, existed in _leaf_changes(root_b, root_a, []):
        top = registry.root.fields.get(str(path[0]))
        kind = ChangeKind.ROOT_UPDATE if top is not None else ChangeKind.INVALID_PROPERTY
        result.changes.append(
            ChangeItem(
                kind=kind,
                page=page_key,
                prop_path=path,
                before=b,
                after=a,
                detail="" if top else f"root has no property {path[0]!r}",
            )
        )
        result.ops.append(SetRootPropOp(page=page_key, path=path, value=a, expect=b if existed else None, has_expect=True))

    idx_b = index_components(page_key, base, registry)
    idx_a = index_components(page_key, proposed, registry)

    removed = [idx_b[i] for i in idx_b if i not in idx_a]
    added = [idx_a[i] for i in idx_a if i not in idx_b]
    common = [i for i in idx_a if i in idx_b]

    # Removals (outermost first is irrelevant: removing a parent removes its slots)
    removed_ids = {loc.id for loc in removed}
    for loc in removed:
        parent_removed = loc.container.startswith("slot:") and loc.container.split(":")[1] in removed_ids
        result.changes.append(
            ChangeItem(
                kind=ChangeKind.COMPONENT_REMOVED, page=page_key, component_id=loc.id, component_type=loc.type, before=loc.type
            )
        )
        if not parent_removed:
            result.ops.append(RemoveComponentOp(page=page_key, component_id=loc.id, expect_type=loc.type))

    # Type changes are a remove + add with the same id.
    retyped = {i for i in common if idx_b[i].type != idx_a[i].type}
    for cid in retyped:
        kind = ChangeKind.COMPONENT_TYPE_CHANGED if registry.allowed(idx_a[cid].type) else ChangeKind.UNSUPPORTED_COMPONENT
        result.changes.append(
            ChangeItem(
                kind=kind,
                page=page_key,
                component_id=cid,
                component_type=idx_a[cid].type,
                before=idx_b[cid].type,
                after=idx_a[cid].type,
            )
        )
        result.ops.append(RemoveComponentOp(page=page_key, component_id=cid, expect_type=idx_b[cid].type))

    added_ids = {loc.id for loc in added} | retyped
    for loc in added:
        if not registry.allowed(loc.type):
            result.changes.append(
                ChangeItem(
                    kind=ChangeKind.UNSUPPORTED_COMPONENT,
                    page=page_key,
                    component_id=loc.id,
                    component_type=loc.type,
                    detail=f"{loc.type} is not a registered client component",
                )
            )
        result.changes.append(
            ChangeItem(
                kind=ChangeKind.COMPONENT_ADDED, page=page_key, component_id=loc.id, component_type=loc.type, after=loc.type
            )
        )

    # Placement: walk proposed containers in order so anchors already exist when used.
    containers_b = {k: container_ids(v) for k, v in iter_containers(base, registry)}
    for key, items in iter_containers(proposed, registry):
        ids_a = container_ids(items)
        ids_b = containers_b.get(key, [])
        stable_common = [i for i in ids_a if i in idx_b and i not in retyped and idx_b[i].container == key]
        # Several reorders can be equally minimal; prefer reporting the components that were explicitly moved.
        anchored = [i for i in stable_common if i not in moved_hint]
        in_order = _lcs([i for i in ids_b if i in anchored], anchored)
        for pos, cid in enumerate(ids_a):
            anchor_after = ids_a[pos - 1] if pos > 0 else None
            if cid in added_ids:
                parent_added = key.startswith("slot:") and key.split(":")[1] in added_ids
                if parent_added:
                    continue  # nested inside an added component: carried with its parent
                comp = idx_a[cid].component
                result.ops.append(
                    AddComponentOp(
                        page=page_key,
                        component=comp,
                        container=key,
                        after_id=anchor_after,
                        index=0 if anchor_after is None else None,
                    )
                )
            elif cid not in in_order:
                moved_from = idx_b[cid].container
                result.changes.append(
                    ChangeItem(
                        kind=ChangeKind.COMPONENT_REORDERED,
                        page=page_key,
                        component_id=cid,
                        component_type=idx_a[cid].type,
                        before=idx_b[cid].index,
                        after=pos,
                        detail=f"moved within {key}" if moved_from == key else f"moved from {moved_from} to {key}",
                    )
                )
                result.ops.append(
                    MoveComponentOp(
                        page=page_key,
                        component_id=cid,
                        to_container=key,
                        after_id=anchor_after,
                        to_index=0 if anchor_after is None else None,
                    )
                )

    # Property changes on surviving components --------------------------
    for cid in common:
        if cid in retyped:
            continue
        loc_b: Located = idx_b[cid]
        loc_a: Located = idx_a[cid]
        slots = _slot_names(registry, loc_a.type)
        props_b = {k: v for k, v in loc_b.component["props"].items() if k not in slots}
        props_a = {k: v for k, v in loc_a.component["props"].items() if k not in slots}
        for path, before, after, existed in _leaf_changes(props_b, props_a, []):
            kind, detail = _classify_prop(registry, loc_a.type, path)
            result.changes.append(
                ChangeItem(
                    kind=kind,
                    page=page_key,
                    component_id=cid,
                    component_type=loc_a.type,
                    prop_path=path,
                    before=before,
                    after=after,
                    detail=detail,
                )
            )
            result.ops.append(_set_prop(page_key, cid, path, before, after, existed))

    # Order ops: removals, then placements, then property edits.
    order = {RemoveComponentOp: 0, AddComponentOp: 1, MoveComponentOp: 1, SetRootPropOp: 2, SetPropOp: 2}
    result.ops.sort(key=lambda op: order[type(op)])
    return result


def diff_sites(
    base: dict[str, Any], proposed: dict[str, Any], registry: ComponentRegistry, moved_hint: frozenset[str] = frozenset()
) -> ConfigDiff:
    result = ConfigDiff()
    pages_b = base.get("pages", {})
    pages_a = proposed.get("pages", {})
    for page in sorted(set(pages_b) | set(pages_a)):
        if page not in pages_a:
            result.changes.append(ChangeItem(kind=ChangeKind.PAGE_REMOVED, page=page))
            result.ops.append(RemovePageOp(page=page))
            continue
        if page not in pages_b:
            result.changes.append(ChangeItem(kind=ChangeKind.PAGE_ADDED, page=page))
            empty = {"root": {"props": {}}, "content": []}
            sub = diff_pages(page, empty, pages_a[page], registry)
            result.changes += [c for c in sub.changes if c.kind != ChangeKind.ROOT_UPDATE]
            result.ops.append(AddPageOp(page=page, data=pages_a[page]))
            continue
        sub = diff_pages(page, pages_b[page], pages_a[page], registry, moved_hint)
        result.changes += sub.changes
        result.ops += sub.ops
    return result
