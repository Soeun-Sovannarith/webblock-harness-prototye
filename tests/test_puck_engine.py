"""Normalisation, structural diff, targeted operations and schema validation."""

from __future__ import annotations

import copy
import json

import pytest

from conftest import pottery_site
from webblock_harness.domain import (
    AddComponentOp,
    ChangeKind,
    MoveComponentOp,
    RemoveComponentOp,
    SetPropOp,
)
from webblock_harness.puck.diff import diff_sites
from webblock_harness.puck.normalize import ConfigFormatError, config_hash, normalize_site
from webblock_harness.puck.ops import OpConflict, apply_ops
from webblock_harness.puck.validate import validate_site
from webblock_harness.registry.registry import load_base_registry

REG = load_base_registry()


@pytest.fixture
def site() -> dict:
    return normalize_site(pottery_site())


def test_normalization_ignores_key_order_whitespace_and_float_format(site: dict) -> None:
    raw = json.dumps(pottery_site(), indent=4, sort_keys=False)
    shuffled = json.loads(raw)
    shuffled["pages"]["/"]["content"][3]["props"]["products"][0]["price"] = 12.0
    shuffled["pages"] = dict(reversed(list(shuffled["pages"].items())))
    assert config_hash(normalize_site(shuffled)) == config_hash(site)
    assert config_hash(normalize_site(raw)) == config_hash(site)


def test_single_page_and_legacy_root_are_accepted() -> None:
    page = {"root": {"title": "Home"}, "content": []}
    assert normalize_site(page) == {"pages": {"/": {"root": {"props": {"title": "Home"}}, "content": []}}}
    with pytest.raises(ConfigFormatError):
        normalize_site("{not json")
    with pytest.raises(ConfigFormatError):
        normalize_site({"pages": []})


def test_diff_reports_minimal_reorder_not_delete_insert(site: dict) -> None:
    proposed = copy.deepcopy(site)
    c = proposed["pages"]["/"]["content"]
    c.insert(1, c.pop(5))  # move the contact form up
    d = diff_sites(site, proposed, REG)
    assert [(x.kind, x.component_id) for x in d.changes] == [(ChangeKind.COMPONENT_REORDERED, "contact-1")]
    assert config_hash(apply_ops(site, d.ops, REG)) == config_hash(proposed)


def test_diff_classifies_content_style_and_invalid(site: dict) -> None:
    proposed = copy.deepcopy(site)
    hero = proposed["pages"]["/"]["content"][1]["props"]
    hero["heading"] = "Clay from the Mekong"
    hero["backgroundColor"] = "#aa5533"
    hero["onClick"] = "alert(1)"  # not part of the schema
    kinds = {(x.kind, tuple(x.prop_path or [])) for x in diff_sites(site, proposed, REG).changes}
    assert (ChangeKind.CONTENT_UPDATE, ("heading",)) in kinds
    assert (ChangeKind.STYLE_UPDATE, ("backgroundColor",)) in kinds
    assert (ChangeKind.INVALID_PROPERTY, ("onClick",)) in kinds


def test_diff_detects_additions_removals_and_unsupported_types(site: dict) -> None:
    proposed = copy.deepcopy(site)
    content = proposed["pages"]["/"]["content"]
    content.pop(4)  # testimonial
    content.append({"type": "Spacer", "props": {"id": "sp-1", "size": 16}})
    content.append({"type": "ThreeDViewer", "props": {"id": "3d-1"}})
    proposed["pages"]["/about"] = {"root": {"props": {"title": "About"}}, "content": []}
    d = diff_sites(site, proposed, REG)
    kinds = {(x.kind, x.component_id or x.page) for x in d.changes}
    assert (ChangeKind.COMPONENT_REMOVED, "testimonial-1") in kinds
    assert (ChangeKind.COMPONENT_ADDED, "sp-1") in kinds
    assert (ChangeKind.UNSUPPORTED_COMPONENT, "3d-1") in kinds
    assert (ChangeKind.PAGE_ADDED, "/about") in kinds
    assert config_hash(apply_ops(site, d.ops, REG)) == config_hash(normalize_site(proposed))


def test_nested_slot_components_are_tracked(site: dict) -> None:
    base = copy.deepcopy(site)
    base["pages"]["/"]["content"].insert(
        2,
        {
            "type": "Columns",
            "props": {"id": "cols", "gap": 8, "left": [{"type": "Text", "props": {"id": "t1", "text": "left"}}], "right": []},
        },
    )
    proposed = copy.deepcopy(base)
    cols = proposed["pages"]["/"]["content"][2]["props"]
    cols["right"].append(cols["left"].pop(0))
    cols["right"][0]["props"]["text"] = "now right"
    d = diff_sites(base, proposed, REG)
    kinds = {(x.kind, x.component_id) for x in d.changes}
    assert (ChangeKind.COMPONENT_REORDERED, "t1") in kinds
    assert (ChangeKind.CONTENT_UPDATE, "t1") in kinds
    assert config_hash(apply_ops(base, d.ops, REG)) == config_hash(proposed)


def test_ops_preserve_unrelated_content_and_enforce_preconditions(site: dict) -> None:
    op = SetPropOp(page="/", component_id="gallery-1", path=["products", 2, "price"], value=70, expect=65, has_expect=True)
    out = apply_ops(site, [op], REG)
    assert out["pages"]["/"]["content"][3]["props"]["products"][2]["price"] == 70
    assert out["pages"]["/shop"] == site["pages"]["/shop"]
    assert site["pages"]["/"]["content"][3]["props"]["products"][2]["price"] == 65  # input untouched
    stale = op.model_copy(update={"expect": 64})
    with pytest.raises(OpConflict) as exc:
        apply_ops(site, [stale], REG)
    assert exc.value.code == "stale_value"


def test_ops_are_atomic_and_validate_anchors(site: dict) -> None:
    ops = [
        MoveComponentOp(page="/", component_id="gallery-1", before_id="desc-1"),
        RemoveComponentOp(page="/", component_id="nope"),
    ]
    with pytest.raises(OpConflict) as exc:
        apply_ops(site, ops, REG)
    assert exc.value.op_index == 1
    with pytest.raises(OpConflict):
        apply_ops(site, [AddComponentOp(page="/", component={"type": "Spacer", "props": {"id": "hero-1"}})], REG)
    with pytest.raises(OpConflict):
        apply_ops(site, [SetPropOp(page="/", component_id="hero-1", path=["id"], value="x")], REG)
    with pytest.raises(OpConflict):
        apply_ops(site, [MoveComponentOp(page="/", component_id="hero-1", before_id="hero-1")], REG)


def test_validation_rejects_unsafe_and_invalid_values(site: dict) -> None:
    assert validate_site(site, REG).status.value == "passed"
    bad = copy.deepcopy(site)
    hero = bad["pages"]["/"]["content"][1]["props"]
    hero["ctaHref"] = "javascript:alert(1)"
    hero["align"] = "diagonal"
    hero["paddingY"] = 9999
    del hero["heading"]
    bad["pages"]["/"]["content"][3]["props"]["products"][0]["price"] = "free"
    bad["pages"]["/"]["content"][3]["props"]["products"][1]["image"] = "data:image/svg+xml,<svg onload=alert(1)>"
    bad["pages"]["/"]["content"].append({"type": "Text", "props": {"id": "hero-1", "text": "dup"}})
    bad["pages"]["/Bad Page"] = {"root": {"props": {"title": "x"}}, "content": []}
    codes = {i.code for i in validate_site(bad, REG).issues}
    assert {
        "unsafe_url",
        "unsupported_value",
        "max",
        "required",
        "type",
        "unsafe_image",
        "duplicate_id",
        "invalid_page_key",
    } <= codes


def test_every_registered_component_default_is_valid() -> None:
    for spec in REG.components.values():
        page = {"root": {"props": {"title": "t"}}, "content": [{"type": spec.name, "props": {"id": "x", **spec.default_props}}]}
        result = validate_site({"pages": {"/": page}}, REG)
        assert result.status.value == "passed", (spec.name, [i.message for i in result.issues])
