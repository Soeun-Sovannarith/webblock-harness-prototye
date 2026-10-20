"""Website templates: onboarding-form data → initial Puck site configuration.

Templates only compose components from the trusted registry, so every generated
configuration validates and renders with the components shipped in the site scaffold.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    price: float = Field(ge=0, le=10_000_000)
    image: str | None = Field(default=None, max_length=500)


BUSINESS_TYPES = [
    "Restaurant",
    "Café",
    "Bakery",
    "Salon",
    "Barbershop",
    "Fitness",
    "Retail shop",
    "Handmade & crafts",
    "Electronics",
    "Professional services",
    "Personal business",
    "Other",
]


class SiteProfile(BaseModel):
    """What the business owner enters in the onboarding form (Feature 03)."""

    model_config = ConfigDict(extra="forbid")
    business_name: str = Field(min_length=1, max_length=80)
    business_type: str | None = Field(default=None, max_length=60)
    tagline: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    logo_url: str | None = Field(default=None, max_length=500, pattern=r"^(https://[^\s<>\"']+|/[^/\s<>\"'][^\s<>\"']*)$")
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=200)
    opening_hours: str | None = Field(default=None, max_length=1000)
    currency: Literal["USD", "KHR"] = "USD"
    products: list[ProductInput] = Field(default_factory=list, max_length=24)


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    theme: str
    best_for: str
    build: Callable[[SiteProfile], dict[str, Any]]
    # Business-specific product fields: drive the generated validation, Business Portal form and site_settings.
    custom_fields: tuple[dict[str, Any], ...] = ()


KHR_PER_USD = 4000  # sample prices only; real prices come from the form


def _products(profile: SiteProfile, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not profile.products:
        rate = KHR_PER_USD if profile.currency == "KHR" else 1
        return [{**p, "price": round(p["price"] * rate, 2)} for p in samples]
    out = []
    for p in profile.products:
        item: dict[str, Any] = {"name": p.name, "price": p.price}
        if p.description:
            item["description"] = p.description
        if p.image:
            item["image"] = p.image
        out.append(item)
    return out


def _footer_text(profile: SiteProfile) -> str:
    parts = [f"© {datetime.now(UTC).year} {profile.business_name}"]
    parts += [x for x in (profile.address, profile.phone, profile.email) if x]
    return " · ".join(parts)[:300]


def _root(profile: SiteProfile, theme: str, title_suffix: str = "") -> dict[str, Any]:
    props: dict[str, Any] = {"title": f"{profile.business_name}{title_suffix}"[:120], "theme": theme}
    if profile.tagline or profile.description:
        props["description"] = (profile.tagline or profile.description)[:300]
    return {"props": props}


def _contact(profile: SiteProfile, cid: str, heading: str) -> dict[str, Any]:
    props: dict[str, Any] = {
        "id": cid,
        "heading": heading,
        "submitLabel": "Send message",
        "successMessage": "Thanks! Your email app will open so you can send the message.",
    }
    if profile.email:
        props["email"] = profile.email
    return {"type": "ContactForm", "props": props}


def _nav(profile: SiteProfile, cid: str, links: list[tuple[str, str]]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "id": cid,
        "brand": profile.business_name,
        "sticky": True,
        "links": [{"label": label, "href": href} for label, href in links],
    }
    if profile.logo_url:
        props["logo"] = profile.logo_url
    return {"type": "Navbar", "props": props}


def _contact_info(profile: SiteProfile, cid: str, heading: str = "Visit or contact us") -> dict[str, Any]:
    """Live from the Business Portal; these values are the editor preview and the no-database fallback."""
    props: dict[str, Any] = {"id": cid, "heading": heading, "source": "database", "contacts": []}
    for key, value in (
        ("address", profile.address),
        ("phone", profile.phone),
        ("email", profile.email),
        ("hours", profile.opening_hours),
    ):
        if value:
            props[key] = value
    return {"type": "ContactInfo", "props": props}


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

_ARTISAN_SAMPLES = [
    {"name": "Lotus Tea Cup", "description": "Small wheel-thrown cup with a celadon glaze.", "price": 12},
    {"name": "Mekong Serving Bowl", "description": "Wide bowl glazed river blue.", "price": 38},
    {"name": "Angkor Vase", "description": "Tall vase with carved temple motifs.", "price": 65},
]


def _artisan(profile: SiteProfile) -> dict[str, Any]:
    products = _products(profile, _ARTISAN_SAMPLES)
    home = {
        "root": _root(profile, "clay"),
        "content": [
            _nav(profile, "nav-1", [("Shop", "/shop"), ("About", "#about"), ("Contact", "#contact")]),
            {
                "type": "Hero",
                "props": {
                    "id": "hero-1",
                    "heading": profile.tagline or f"Handmade by {profile.business_name}",
                    "subheading": (profile.description or "Made by hand, one piece at a time.")[:300],
                    "ctaLabel": "Shop the collection",
                    "ctaHref": "/shop",
                    "align": "center",
                    "paddingY": 96,
                },
            },
            {
                "type": "ProductDescription",
                "props": {
                    "id": "about-1",
                    "title": f"About {profile.business_name}"[:120],
                    "body": profile.description or "Tell visitors your story: who makes your pieces, where, and how.",
                },
            },
            {
                "type": "ProductGallery",
                "props": {
                    "source": "database",
                    "id": "gallery-1",
                    "title": "Featured pieces",
                    "columns": 3,
                    "currency": profile.currency,
                    "products": products[:6],
                },
            },
            {
                "type": "Testimonial",
                "props": {
                    "id": "testimonial-1",
                    "quote": "Beautiful work — every piece feels personal.",
                    "author": "A happy customer",
                },
            },
            _contact_info(profile, "location-1"),
            _contact(profile, "contact-1", "Order a custom piece"),
            {"type": "Footer", "props": {"id": "footer-1", "text": _footer_text(profile), "links": []}},
        ],
    }
    shop = {
        "root": _root(profile, "clay", " — Shop"),
        "content": [
            _nav(profile, "shop-nav", [("Home", "/"), ("Contact", "/#contact")]),
            {"type": "Heading", "props": {"id": "shop-heading", "text": "All pieces", "level": 1, "align": "left"}},
            {
                "type": "ProductGallery",
                "props": {
                    "source": "database",
                    "id": "shop-gallery",
                    "columns": 3,
                    "currency": profile.currency,
                    "products": products,
                },
            },
            {"type": "Footer", "props": {"id": "shop-footer", "text": _footer_text(profile), "links": []}},
        ],
    }
    return {"pages": {"/": home, "/shop": shop}}


_CAFE_SAMPLES = [
    {
        "name": "Iced Palm Sugar Latte",
        "description": "Cambodian palm sugar, espresso and fresh milk.",
        "price": 3.5,
        "image": "https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=800&q=80",
    },
    {
        "name": "Single-Origin Pour Over",
        "description": "Bright, floral, brewed to order.",
        "price": 4,
        "image": "https://images.unsplash.com/photo-1511920170033-f8396924c348?w=800&q=80",
    },
    {
        "name": "House Blend Beans (250g)",
        "description": "Chocolatey and smooth, roasted weekly.",
        "price": 9,
        "image": "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?w=800&q=80",
    },
]


def _cafe(profile: SiteProfile) -> dict[str, Any]:
    hours = profile.opening_hours or "Open daily 7:00 – 18:00."
    visit = "\n\n".join(x for x in (hours, profile.address or "", profile.phone or "") if x)
    return {
        "pages": {
            "/": {
                "root": _root(profile, "earth"),
                "content": [
                    _nav(profile, "nav-1", [("Menu", "#products"), ("Visit", "#visit"), ("Contact", "#contact")]),
                    {
                        "type": "Hero",
                        "props": {
                            "id": "hero-1",
                            "heading": profile.tagline or f"Welcome to {profile.business_name}",
                            "subheading": (profile.description or "Freshly brewed, made with care.")[:300],
                            "ctaLabel": "See the menu",
                            "ctaHref": "#products",
                            "align": "left",
                            "paddingY": 96,
                        },
                    },
                    {
                        "type": "ProductGallery",
                        "props": {
                            "source": "database",
                            "id": "menu-1",
                            "title": "Menu",
                            "columns": 3,
                            "currency": profile.currency,
                            "products": _products(profile, _CAFE_SAMPLES),
                        },
                    },
                    {
                        "type": "Columns",
                        "props": {
                            "id": "visit-1",
                            "gap": 32,
                            "left": [
                                {
                                    "type": "Heading",
                                    "props": {"id": "visit-heading", "text": "Visit us", "level": 2, "align": "left"},
                                },
                                {"type": "Text", "props": {"id": "visit-text", "text": visit, "align": "left", "size": "md"}},
                            ],
                            "right": [
                                {
                                    "type": "Image",
                                    "props": {
                                        "id": "visit-image",
                                        "src": "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?w=1200&q=80",
                                        "alt": f"Inside {profile.business_name}"[:200],
                                        "rounded": True,
                                    },
                                },
                            ],
                        },
                    },
                    _contact_info(profile, "location-1", "Find us"),
                    _contact(profile, "contact-1", "Book a table or ask a question"),
                    {"type": "Footer", "props": {"id": "footer-1", "text": _footer_text(profile), "links": []}},
                ],
            }
        }
    }


_MODERN_SAMPLES = [
    {"name": "Wireless Earbuds Pro", "description": "Active noise cancelling, 30h battery.", "price": 79},
    {"name": "Mechanical Keyboard", "description": "Hot-swappable switches, RGB backlight.", "price": 99},
    {"name": "USB-C Hub 7-in-1", "description": "HDMI 4K, SD, 100W passthrough.", "price": 35},
    {"name": "Smart Watch S2", "description": "Heart-rate, GPS, 7-day battery.", "price": 129},
]


def _modern(profile: SiteProfile) -> dict[str, Any]:
    products = _products(profile, _MODERN_SAMPLES)
    return {
        "pages": {
            "/": {
                "root": _root(profile, "dark"),
                "content": [
                    _nav(profile, "nav-1", [("Products", "/shop"), ("Why us", "#about"), ("Contact", "#contact")]),
                    {
                        "type": "Hero",
                        "props": {
                            "id": "hero-1",
                            "heading": profile.tagline or profile.business_name,
                            "subheading": (profile.description or "Quality gear, honest prices, fast delivery.")[:300],
                            "ctaLabel": "Browse products",
                            "ctaHref": "/shop",
                            "align": "center",
                            "paddingY": 112,
                        },
                    },
                    {
                        "type": "ProductGallery",
                        "props": {
                            "source": "database",
                            "id": "gallery-1",
                            "title": "Best sellers",
                            "columns": 4,
                            "currency": profile.currency,
                            "products": products[:4],
                        },
                    },
                    {
                        "type": "Text",
                        "props": {
                            "id": "about-1",
                            "align": "center",
                            "size": "lg",
                            "text": profile.description or "Every product is tested by our team before it ships.",
                        },
                    },
                    {
                        "type": "Button",
                        "props": {"id": "cta-1", "label": "See all products", "href": "/shop", "variant": "secondary"},
                    },
                    _contact_info(profile, "location-1", "Store & support"),
                    _contact(profile, "contact-1", "Questions? Talk to us"),
                    {"type": "Footer", "props": {"id": "footer-1", "text": _footer_text(profile), "links": []}},
                ],
            },
            "/shop": {
                "root": _root(profile, "dark", " — Products"),
                "content": [
                    _nav(profile, "shop-nav", [("Home", "/"), ("Contact", "/#contact")]),
                    {"type": "Heading", "props": {"id": "shop-heading", "text": "All products", "level": 1, "align": "left"}},
                    {
                        "type": "ProductGallery",
                        "props": {
                            "source": "database",
                            "id": "shop-gallery",
                            "columns": 4,
                            "currency": profile.currency,
                            "products": products,
                        },
                    },
                    {"type": "Footer", "props": {"id": "shop-footer", "text": _footer_text(profile), "links": []}},
                ],
            },
        }
    }


TEMPLATES: dict[str, Template] = {
    t.id: t
    for t in [
        Template(
            "artisan-shop",
            "Artisan Shop",
            "Warm, handmade look with a home page and a shop page.",
            "clay",
            "Pottery, crafts, boutiques",
            _artisan,
            (
                {"key": "materials", "label": "Materials", "type": "text"},
                {"key": "madeToOrder", "label": "Made to order", "type": "boolean"},
                {"key": "stock", "label": "In stock", "type": "number"},
            ),
        ),
        Template(
            "cafe",
            "Café & Kitchen",
            "Earthy one-page site with a menu, opening hours and a contact form.",
            "earth",
            "Cafés, restaurants, bakeries",
            _cafe,
            (
                {
                    "key": "category",
                    "label": "Menu category",
                    "type": "select",
                    "options": ["Coffee", "Tea", "Food", "Dessert", "Beans & merch"],
                },
                {"key": "available", "label": "Available today", "type": "boolean"},
            ),
        ),
        Template(
            "modern-store",
            "Modern Store",
            "Dark, product-first storefront with a full product page.",
            "dark",
            "Electronics, gadgets, fashion",
            _modern,
            (
                {"key": "sku", "label": "SKU", "type": "text"},
                {"key": "brand", "label": "Brand", "type": "text"},
                {"key": "stock", "label": "In stock", "type": "number"},
                {"key": "warrantyMonths", "label": "Warranty (months)", "type": "number"},
            ),
        ),
    ]
}


def seed_data(profile: SiteProfile, config: dict[str, Any]) -> dict[str, Any]:
    """Initial database content (webblock/seed.json): the business details and the products shown on the site."""
    products: list[dict[str, Any]] = []
    seen: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            if node.get("type") == "ProductGallery":
                for p in node.get("props", {}).get("products", []):
                    if p.get("name") and p["name"] not in seen:
                        seen.add(p["name"])
                        custom = {k: p[k] for k in ("description", "badge") if p.get(k)}
                        if p.get("image"):
                            custom["imageUrl"] = p["image"]
                        products.append({"title": p["name"], "price": p.get("price", 0), "customFields": custom})
            for value in node.values():
                collect(value)

    collect(config)
    business = {
        "businessName": profile.business_name,
        "businessType": profile.business_type,
        "description": profile.description or None,
        "logoUrl": profile.logo_url,
        "email": profile.email,
        "phone": profile.phone,
        "address": profile.address,
        "openingHours": profile.opening_hours,
        "currency": profile.currency,
    }
    return {"business": business, "products": products, "contacts": []}


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].strip("-")) or "site"
