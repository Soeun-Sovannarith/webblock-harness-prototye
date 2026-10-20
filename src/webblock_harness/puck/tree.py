"""Navigation helpers for Puck data.

A *site configuration* is ``{"pages": {"/": <PuckData>, "/shop": <PuckData>}}``
where each ``PuckData`` follows Puck's format::

    {"root": {"props": {...}}, "content": [{"type": "Hero", "props": {"id": "...", ...}}], "zones": {...}}

Components live in *containers*, addressed by a stable key:

* ``content``                       – the page's top-level list
* ``zone:<zoneKey>``                – legacy DropZone lists (``data.zones["<parentId>:<zone>"]``)
* ``slot:<componentId>:<propName>`` – slot fields (lists of components inside props)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from webblock_harness.registry.registry import ComponentRegistry

Component = dict[str, Any]
PageData = dict[str, Any]
SiteConfig = dict[str, Any]


@dataclass(frozen=True)
class Located:
    page: str
    container: str
    index: int
    component: Component

    @property
    def id(self) -> str:
        return str(self.component["props"]["id"])

    @property
    def type(self) -> str:
        return str(self.component["type"])


def is_component(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("type"), str) and isinstance(value.get("props"), dict)


def _slot_props(component: Component, registry: ComponentRegistry | None) -> list[str]:
    props = component["props"]
    spec = registry.get(component["type"]) if registry else None
    if spec is not None:
        return [name for name, f in spec.fields.items() if f.type == "slot" and isinstance(props.get(name), list)]
    # Unknown component: treat any list made only of components as a slot.
    return [name for name, v in props.items() if isinstance(v, list) and v and all(is_component(x) for x in v)]


def iter_containers(page: PageData, registry: ComponentRegistry | None = None) -> Iterator[tuple[str, list[Any]]]:
    """Yield ``(container_key, list)`` for every component container on a page, depth first."""

    def walk(key: str, items: list[Any]) -> Iterator[tuple[str, list[Any]]]:
        yield key, items
        for item in items:
            if is_component(item):
                for slot in _slot_props(item, registry):
                    yield from walk(f"slot:{item['props'].get('id')}:{slot}", item["props"][slot])

    content = page.get("content")
    if isinstance(content, list):
        yield from walk("content", content)
    zones = page.get("zones")
    if isinstance(zones, dict):
        for zone_key in sorted(zones):
            if isinstance(zones[zone_key], list):
                yield from walk(f"zone:{zone_key}", zones[zone_key])


def index_components(page_key: str, page: PageData, registry: ComponentRegistry | None = None) -> dict[str, Located]:
    found: dict[str, Located] = {}
    for container, items in iter_containers(page, registry):
        for i, item in enumerate(items):
            if is_component(item) and isinstance(item["props"].get("id"), str):
                found.setdefault(item["props"]["id"], Located(page_key, container, i, item))
    return found


def get_container(page: PageData, key: str, registry: ComponentRegistry | None = None) -> list[Any] | None:
    for container, items in iter_containers(page, registry):
        if container == key:
            return items
    if key.startswith("zone:"):
        zones = page.setdefault("zones", {})
        return zones.setdefault(key.removeprefix("zone:"), [])  # type: ignore[no-any-return]
    return None


def container_ids(items: list[Any]) -> list[str]:
    return [str(c["props"]["id"]) for c in items if is_component(c) and "id" in c["props"]]


def display_name(component: Component) -> str:
    props = component.get("props", {})
    for key in ("title", "heading", "name", "label", "text", "brand"):
        if isinstance(props.get(key), str) and props[key].strip():
            return str(props[key])[:60]
    return str(component.get("type"))
