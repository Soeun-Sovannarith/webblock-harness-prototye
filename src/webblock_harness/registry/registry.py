"""Trusted component registry.

The registry is the source of truth for which Puck components exist, their
property schemas, and whether they may appear in client-generated pages. The
base registry ships with the harness (``default_registry.json`` or the file
named by ``COMPONENT_REGISTRY_PATH``); Path B may *propose* project-specific
components, which only become ``active`` once the code implementing them has
been deployed and verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from webblock_harness.domain import ComponentSpec, FieldSpec

_DEFAULT_PATH = Path(__file__).with_name("default_registry.json")
_COMPONENT_NAME = re.compile(r"^[A-Z][A-Za-z0-9]{1,63}$")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ComponentRegistry:
    root: ComponentSpec
    components: dict[str, ComponentSpec]

    def get(self, name: str) -> ComponentSpec | None:
        return self.components.get(name)

    def allowed(self, name: str) -> bool:
        spec = self.components.get(name)
        return spec is not None and spec.allowed_in_client_pages

    @property
    def hash(self) -> str:
        payload = {
            "root": self.root.model_dump(mode="json"),
            "components": {k: v.model_dump(mode="json") for k, v in sorted(self.components.items())},
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    def with_extensions(self, specs: list[ComponentSpec]) -> ComponentRegistry:
        merged = dict(self.components)
        for spec in specs:
            if spec.name in self.components:
                # Project extensions can never shadow a trusted base component.
                continue
            merged[spec.name] = spec
        return ComponentRegistry(root=self.root, components=merged)

    def find_by_reference(self, phrase: str) -> list[ComponentSpec]:
        """Components whose name/label/aliases match a natural-language reference."""
        phrase_n = _norm_words(phrase)
        if not phrase_n:
            return []
        exact: list[ComponentSpec] = []
        partial: list[ComponentSpec] = []
        for spec in self.components.values():
            names = {_norm_words(_split_camel(spec.name)), _norm_words(spec.label)} | {_norm_words(a) for a in spec.aliases}
            if phrase_n in names:
                exact.append(spec)
            elif any(n and (n in phrase_n.split() or f" {n} " in f" {phrase_n} ") for n in names if len(n) > 3):
                partial.append(spec)
        return exact or partial

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "label": s.label,
                "capabilities": s.capabilities,
                "fields": {k: f.model_dump(mode="json", exclude_none=True) for k, f in s.fields.items()},
                "allowed_in_client_pages": s.allowed_in_client_pages,
            }
            for s in self.components.values()
        ]


def _split_camel(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name)


def _norm_words(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = [w for w in text.split() if w not in {"the", "a", "an", "section", "block", "component", "my", "our"}]
    # crude singularisation so "products" ~ "product"
    words = [w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w for w in words]
    return " ".join(words)


def validate_component_spec(spec: ComponentSpec) -> list[str]:
    """Structural checks applied to any proposed (LLM-generated) component spec."""
    problems: list[str] = []
    if not _COMPONENT_NAME.match(spec.name):
        problems.append(f"Component name {spec.name!r} must be PascalCase alphanumeric.")
    if not spec.fields:
        problems.append("Component must declare at least one field.")
    for field_name, field in spec.fields.items():
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9]{0,63}$", field_name):
            problems.append(f"Field name {field_name!r} is not a safe identifier.")
        if field_name == "id":
            problems.append("Field 'id' is reserved by Puck.")
        if field.type == "select" and not field.options:
            problems.append(f"Select field {field_name!r} must declare options.")
        if field.type in ("array", "object") and not field.item_fields:
            problems.append(f"{field.type} field {field_name!r} must declare item_fields.")
    for key in spec.default_props:
        if key not in spec.fields:
            problems.append(f"default_props key {key!r} is not a declared field.")
    return problems


def _load_spec(raw: dict[str, Any]) -> ComponentSpec:
    raw = dict(raw)
    raw["fields"] = {k: FieldSpec.model_validate(v) for k, v in raw["fields"].items()}
    return ComponentSpec.model_validate(raw)


def load_base_registry(path: Path | None = None) -> ComponentRegistry:
    path = path or Path(os.environ.get("COMPONENT_REGISTRY_PATH") or _DEFAULT_PATH)
    data = json.loads(path.read_text(encoding="utf-8"))
    root = _load_spec(data["root"])
    components = {c["name"]: _load_spec(c) for c in data["components"]}
    return ComponentRegistry(root=root, components=components)
