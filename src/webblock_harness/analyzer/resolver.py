"""Rules-based translation of common natural-language edits into Puck operations.

Covers the high-volume, well-understood requests (price changes, quoted text
replacement, reordering, adding/removing registered components). Anything it
cannot resolve *unambiguously* is returned as either "not matched" (so the LLM
planner may try) or as clarification questions — it never guesses a target.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from webblock_harness.domain import (
    AddComponentOp,
    ClarificationQuestion,
    ComponentSpec,
    MoveComponentOp,
    PuckOp,
    RemoveComponentOp,
    SetPropOp,
)
from webblock_harness.puck.tree import Located, display_name, index_components
from webblock_harness.registry.registry import ComponentRegistry, _norm_words


@dataclass
class Resolution:
    matched: bool = False
    rule: str | None = None
    ops: list[PuckOp] = field(default_factory=list)
    clarifications: list[ClarificationQuestion] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Component references
# --------------------------------------------------------------------------


def _all_components(config: dict[str, Any], registry: ComponentRegistry) -> list[Located]:
    out: list[Located] = []
    for page_key, page in config["pages"].items():
        out += index_components(page_key, page, registry).values()
    return out


def _page_from_phrase(phrase: str | None, config: dict[str, Any]) -> str | None:
    if not phrase:
        return None
    p = _norm_words(phrase)
    if p in ("home", "homepage", "home page", "main", "landing", "front"):
        return "/" if "/" in config["pages"] else None
    for key in config["pages"]:
        if _norm_words(key.strip("/").replace("-", " ")) == p:
            return key
    return None


def resolve_components(
    phrase: str, config: dict[str, Any], registry: ComponentRegistry, page: str | None = None
) -> list[Located]:
    phrase_n = _norm_words(phrase)
    if not phrase_n:
        return []
    candidates = [c for c in _all_components(config, registry) if page is None or c.page == page]
    # 1. Exact display-name match ("the Lotus Tea Cup card")
    by_name = [
        c for c in candidates if _norm_words(display_name(c.component)) and _norm_words(display_name(c.component)) in phrase_n
    ]
    # 2. Registry type/alias match ("the product gallery")
    types = {s.name for s in registry.find_by_reference(phrase)}
    by_type = [c for c in candidates if c.type in types]
    if by_type and by_name:
        both = [c for c in by_type if c in by_name]
        if both:
            return both
    return by_type or by_name


def _describe(loc: Located) -> str:
    return f"{loc.type} '{display_name(loc.component)}' (id {loc.id}, page {loc.page})"


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

_NUM = r"(?:[$៛]\s?)?(\d{1,7}(?:[.,]\d{1,2})?)\s*(?:usd|dollars?|\$|riel|khr)?"
_PRICE_FROM_TO = re.compile(rf"\bfrom\s+{_NUM}\s+to\s+{_NUM}", re.IGNORECASE)
_PRICE_TO = re.compile(rf"\bprice\b.*?\bto\s+{_NUM}\s*[.!]?\s*$", re.IGNORECASE)
_PRICE_TARGET = re.compile(r"\bprice\s+(?:of|for)\s+(?:the\s+)?(?P<target>.+?)\s+(?:from|to)\b", re.IGNORECASE)
_PRICE_TARGET_POSSESSIVE = re.compile(
    r"\b(?:change|update|set|make)\s+(?:the\s+)?(?P<target>.+?)(?:'s|’s)?\s+price\b", re.IGNORECASE
)
_QUOTED_FROM_TO = re.compile(
    r"[\"“'‘](?P<old>[^\"”'’]{1,300})[\"”'’]\s+(?:to|with|into)\s+[\"“'‘](?P<new>[^\"”'’]{0,2000})[\"”'’]"
)
_MOVE = re.compile(
    r"\b(?:move|put|place|shift)\s+(?:the\s+)?(?P<what>.+?)\s+(?P<rel>above|before|below|after|under|underneath|beneath|"
    r"to the top|to the bottom|at the top|at the bottom|to the end)(?:\s+(?:of\s+)?(?:the\s+)?(?P<anchor>.+?))?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_SWAP = re.compile(r"\bswap\s+(?:the\s+)?(?P<a>.+?)\s+and\s+(?:the\s+)?(?P<b>.+?)\s*[.!]?\s*$", re.IGNORECASE)
_ADD = re.compile(
    r"\b(?:add|insert|include)\s+(?:a\s+|an\s+|another\s+|new\s+|the\s+)*(?P<what>.+?)"
    r"(?:\s+(?P<rel>above|before|below|after|under|beneath|to the top|at the top|to the bottom|at the bottom|at the end)"
    r"(?:\s+(?:of\s+)?(?:the\s+)?(?P<anchor>.+?))?)?(?:\s+(?:on|to)\s+(?:the\s+)?(?P<page>[a-z\- ]+?)\s+page)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_REMOVE = re.compile(
    r"\b(?:remove|delete|hide|take out|get rid of)\s+(?:the\s+)?(?P<what>.+?)(?:\s+from\s+(?:the\s+)?(?P<page>[a-z\- ]+?)\s+page)?\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _to_number(raw: str) -> float | int:
    value = float(raw.replace(",", "."))
    return int(value) if value.is_integer() else value


def _price_slots(config: dict[str, Any], registry: ComponentRegistry) -> list[tuple[Located, list[str | int], Any, str]]:
    """Every price-like value: (component, prop path, value, human label)."""
    slots: list[tuple[Located, list[str | int], Any, str]] = []
    for loc in _all_components(config, registry):
        spec = registry.get(loc.type)
        if spec is None:
            continue
        props = loc.component["props"]
        for fname, f in spec.fields.items():
            if f.semantic == "price" and fname in props:
                slots.append((loc, [fname], props[fname], display_name(loc.component)))
            if f.type == "array" and f.item_fields and isinstance(props.get(fname), list):
                price_keys = [k for k, sf in f.item_fields.items() if sf.semantic == "price"]
                title_keys = [k for k, sf in f.item_fields.items() if sf.semantic == "title"]
                for i, item in enumerate(props[fname]):
                    if not isinstance(item, dict):
                        continue
                    label = next((str(item[k]) for k in title_keys if item.get(k)), f"item {i + 1}")
                    for pk in price_keys:
                        if pk in item:
                            slots.append((loc, [fname, i, pk], item[pk], label))
    return slots


def _rule_price(text: str, config: dict[str, Any], registry: ComponentRegistry) -> Resolution | None:
    if not re.search(r"\b(price|prices|cost|costs)\b", text, re.IGNORECASE) and not _PRICE_FROM_TO.search(text):
        return None
    old: float | int | None = None
    m = _PRICE_FROM_TO.search(text)
    if m:
        old, new = _to_number(m.group(1)), _to_number(m.group(2))
    else:
        m2 = _PRICE_TO.search(text)
        if not m2:
            return None
        new = _to_number(m2.group(1))
    target = None
    for rx in (_PRICE_TARGET, _PRICE_TARGET_POSSESSIVE):
        tm = rx.search(text)
        if tm:
            target = tm.group("target")
            break
    slots = _price_slots(config, registry)
    if old is not None:
        slots = [s for s in slots if isinstance(s[2], (int, float)) and s[2] == old]
    if target:
        tn = _norm_words(re.sub(r"\b(price|product|item)\b", "", target, flags=re.IGNORECASE))
        named = [s for s in slots if tn and (tn in _norm_words(s[3]) or _norm_words(s[3]) in tn)]
        if named:
            slots = named
        elif tn and tn not in {"pottery", "pottery product"}:
            slots = []
    res = Resolution(matched=True, rule="price_change")
    if len(slots) == 1:
        loc, path, value, label = slots[0]
        res.ops.append(SetPropOp(page=loc.page, component_id=loc.id, path=path, value=new, expect=value, has_expect=True))
        res.assumptions.append(f"Interpreted as: set the price of '{label}' ({loc.type} {loc.id}) from {value} to {new}.")
        return res
    if not slots:
        what = f" priced {old}" if old is not None else ""
        name = f" named '{target}'" if target else ""
        res.clarifications.append(
            ClarificationQuestion(
                question=f"I could not find a product{name}{what}. Which product should change price?",
                options=[f"{s[3]} ({s[2]})" for s in _price_slots(config, registry)][:10],
            )
        )
        return res
    res.clarifications.append(
        ClarificationQuestion(
            question="Several products match. Which one should change price?",
            options=[f"{s[3]} ({s[2]}) on {s[0].page}" for s in slots][:10],
        )
    )
    return res


def _rule_quoted_text(text: str, config: dict[str, Any], registry: ComponentRegistry) -> Resolution | None:
    m = _QUOTED_FROM_TO.search(text)
    if not m:
        return None
    old, new = m.group("old"), m.group("new")
    hits: list[tuple[Located, list[str | int], str]] = []

    def scan(loc: Located, value: Any, path: list[str | int]) -> None:
        if isinstance(value, str):
            if value.strip().lower() == old.strip().lower() or old in value:
                hits.append((loc, path, value))
        elif isinstance(value, dict):
            for k, v in value.items():
                if k != "id":
                    scan(loc, v, [*path, k])
        elif isinstance(value, list):
            for i, v in enumerate(value):
                if not (isinstance(v, dict) and "type" in v and "props" in v):
                    scan(loc, v, [*path, i])

    for loc in _all_components(config, registry):
        scan(loc, loc.component["props"], [])
    res = Resolution(matched=True, rule="text_replace")
    if len(hits) == 1:
        loc, path, value = hits[0]
        replacement = new if value.strip().lower() == old.strip().lower() else value.replace(old, new)
        res.ops.append(SetPropOp(page=loc.page, component_id=loc.id, path=path, value=replacement, expect=value, has_expect=True))
        res.assumptions.append(f"Replacing text in {_describe(loc)} at {'.'.join(map(str, path))}.")
    elif not hits:
        res.clarifications.append(
            ClarificationQuestion(question=f'I could not find the text "{old}" on the site. Where should it change?')
        )
    else:
        res.clarifications.append(
            ClarificationQuestion(
                question=f'The text "{old}" appears in several places. Which one should change?',
                options=[f"{_describe(h[0])} → {'.'.join(map(str, h[1]))}" for h in hits][:10],
            )
        )
    return res


def _unique(
    phrase: str, config: dict[str, Any], registry: ComponentRegistry, res: Resolution, role: str, page: str | None = None
) -> Located | None:
    found = resolve_components(phrase, config, registry, page)
    if len(found) == 1:
        return found[0]
    if not found:
        res.clarifications.append(
            ClarificationQuestion(question=f'Which section did you mean by "{phrase}" ({role})? I could not find it.')
        )
    else:
        res.clarifications.append(
            ClarificationQuestion(
                question=f'"{phrase}" matches several sections ({role}). Which one?', options=[_describe(c) for c in found][:10]
            )
        )
    return None


def _placement(rel: str, anchor: Located | None) -> dict[str, Any]:
    rel = rel.lower()
    if rel in ("above", "before"):
        assert anchor is not None
        return {"before_id": anchor.id}
    if rel in ("below", "after", "under", "underneath", "beneath"):
        assert anchor is not None
        return {"after_id": anchor.id}
    return {}


def _rule_move(text: str, config: dict[str, Any], registry: ComponentRegistry) -> Resolution | None:
    m = _MOVE.search(text)
    swap = _SWAP.search(text) if not m else None
    if not m and not swap:
        return None
    res = Resolution(matched=True, rule="reorder")
    if swap:
        a = _unique(swap.group("a"), config, registry, res, "first section")
        b = _unique(swap.group("b"), config, registry, res, "second section")
        if a and b:
            if a.page != b.page or a.container != b.container:
                res.clarifications.append(
                    ClarificationQuestion(question="Those sections are in different places; where should each go?")
                )
            else:
                first, second = (a, b) if a.index < b.index else (b, a)
                res.ops.append(MoveComponentOp(page=a.page, component_id=first.id, after_id=second.id))
                if second.index - first.index > 1:
                    res.ops.append(
                        MoveComponentOp(page=a.page, component_id=second.id, to_container=first.container, to_index=first.index)
                    )
        return res
    assert m is not None
    what = _unique(m.group("what"), config, registry, res, "section to move")
    rel = m.group("rel").lower()
    anchor_phrase = m.group("anchor")
    if what is None:
        # "put a gallery below the hero" when no gallery exists is an add, not a move.
        return (
            None
            if not resolve_components(m.group("what"), config, registry) and registry.find_by_reference(m.group("what"))
            else res
        )
    if rel in ("to the top", "at the top"):
        res.ops.append(MoveComponentOp(page=what.page, component_id=what.id, to_container=what.container, to_index=0))
        res.assumptions.append("'Top' means the first position in its section list.")
        return res
    if rel in ("to the bottom", "at the bottom", "to the end"):
        res.ops.append(MoveComponentOp(page=what.page, component_id=what.id, to_container=what.container))
        return res
    if not anchor_phrase:
        res.clarifications.append(ClarificationQuestion(question=f"{rel.capitalize()} which section should it go?"))
        return res
    anchor = _unique(anchor_phrase, config, registry, res, "reference section", page=what.page)
    if anchor is None:
        return res
    if anchor.id == what.id:
        res.clarifications.append(
            ClarificationQuestion(question="The section and its reference are the same; where should it go?")
        )
        return res
    res.ops.append(MoveComponentOp(page=what.page, component_id=what.id, **_placement(rel, anchor)))
    return res


def _new_component_id(spec: ComponentSpec, text: str, config: dict[str, Any], registry: ComponentRegistry) -> str:
    existing = {c.id for c in _all_components(config, registry)}
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    cid = f"{spec.name}-{digest}"
    n = 2
    while cid in existing:
        cid = f"{spec.name}-{digest}-{n}"
        n += 1
    return cid


def _rule_add(text: str, config: dict[str, Any], registry: ComponentRegistry) -> Resolution | None:
    m = _ADD.search(text)
    if not m:
        return None
    specs = registry.find_by_reference(m.group("what"))
    specs = [s for s in specs if s.allowed_in_client_pages]
    if len(specs) != 1:
        return None  # not a registered component: not ours to resolve (may be Path B)
    spec = specs[0]
    res = Resolution(matched=True, rule="add_component")
    page = _page_from_phrase(m.group("page"), config)
    if m.group("page") and page is None:
        res.clarifications.append(
            ClarificationQuestion(question=f'Which page is "{m.group("page")}"?', options=list(config["pages"]))
        )
        return res
    rel, anchor_phrase = m.group("rel"), m.group("anchor")
    placement: dict[str, Any] = {}
    container = "content"
    if rel and anchor_phrase:
        anchor = _unique(anchor_phrase, config, registry, res, "reference section", page=page)
        if anchor is None:
            return res
        page = anchor.page
        container = anchor.container
        placement = _placement(rel, anchor)
    elif rel and rel.lower() in ("to the top", "at the top"):
        placement = {"index": 0}
    if page is None:
        page = "/" if "/" in config["pages"] else next(iter(config["pages"]))
        if len(config["pages"]) > 1:
            res.assumptions.append(f"No page was named; adding to {page}.")
    if not placement:
        res.assumptions.append("No position was given; adding at the end of the page.")
    props = {"id": _new_component_id(spec, text, config, registry), **spec.default_props}
    res.ops.append(AddComponentOp(page=page, component={"type": spec.name, "props": props}, container=container, **placement))
    res.assumptions.append(f"The new {spec.label} starts with its default content; edit it in the visual editor.")
    return res


def _rule_remove(text: str, config: dict[str, Any], registry: ComponentRegistry) -> Resolution | None:
    m = _REMOVE.search(text)
    if not m:
        return None
    res = Resolution(matched=True, rule="remove_component")
    page = _page_from_phrase(m.group("page"), config)
    target = _unique(m.group("what"), config, registry, res, "section to remove", page=page)
    if target:
        res.ops.append(RemoveComponentOp(page=target.page, component_id=target.id, expect_type=target.type))
    return res


def resolve_request(text: str, config: dict[str, Any], registry: ComponentRegistry) -> Resolution:
    """Try each rule in order of specificity; return the first that recognises the request."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\s+(?:and then|then)\s+", text.strip()) if s.strip()]
    if len(sentences) > 1:
        combined = Resolution(matched=True, rule="multi")
        working = config
        from webblock_harness.puck.ops import OpConflict, apply_ops

        for sentence in sentences:
            sub = resolve_request(sentence, working, registry)
            if not sub.matched:
                return Resolution()  # partially understood requests go to the planner as a whole
            combined.ops += sub.ops
            combined.clarifications += sub.clarifications
            combined.assumptions += sub.assumptions
            if sub.ops:
                try:
                    working = apply_ops(working, sub.ops, registry)
                except OpConflict:
                    return Resolution()
        return combined
    for rule in (_rule_price, _rule_quoted_text, _rule_move, _rule_add, _rule_remove):
        res = rule(text, config, registry)
        if res is not None:
            return res
    return Resolution()
