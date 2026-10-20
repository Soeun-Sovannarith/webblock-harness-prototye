"""Deterministic intent signals extracted from a natural-language request.

These rules never decide on their own; they feed the classifier alongside the
structural diff. Keeping them in code (not in an LLM prompt) keeps the business
rules testable and auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from webblock_harness.domain import ApprovalReason


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_INTERACTIVE = _rx(
    r"\b(interactive|customi[sz](e|er|ation|ations|able)\s+(tool|builder|widget|feature)|configurator|calculator|"
    r"drag[- ]and[- ]drop|3d (viewer|model|preview)|animated|live preview|quiz|mini[- ]?game|chat ?bot|wizard|"
    r"real[- ]?time|filter(ing)? (by|products)|search (bar|box)|countdown|carousel|lightbox|zoom(able)?|"
    r"(design|build|personali[sz]e) (your|their) own)\b"
)
_BACKEND = _rx(
    r"\b(api|apis|endpoint|endpoints|backend|back-end|server[- ]side|webhook|booking|bookings|book an?|reservations?|"
    r"appointments?|checkout|shopping cart|add to cart|payments?|pay online|stripe|paypal|aba pay|bakong|"
    r"subscriptions?|newsletter sign ?up|log ?in|sign ?in|sign ?up|user accounts?|authentication|order tracking|"
    r"inventory|stock levels?|send (an )?emails?|email notifications?|sms|integrat(e|ion) with)\b"
)
_DATABASE = _rx(
    r"\b(database|db table|persist(ence|ed|ent)?|store (the |their |all )?(data|bookings|orders|submissions|records|customers)|"
    r"save (the |their |all )?(bookings|orders|submissions|records|data)|schema|migrations?|new table)\b"
)
_AUTH = _rx(r"\b(log ?in|sign ?in|sign ?up|password|user accounts?|authentication|oauth|sso)\b")
_PAYMENT = _rx(r"\b(checkout|payments?|pay online|stripe|paypal|credit card|aba pay|bakong|refunds?)\b")
_TENANT = _rx(r"\b(tenants?|multi-?tenant|other (sites|clients) data)\b")
_DESTRUCTIVE = _rx(
    r"\b((delete|remove|wipe|drop|clear|reset) (all|every|everything|the (entire|whole))|start (over|from scratch)|"
    r"(rebuild|regenerate|rewrite|redo) (the |my )?(entire|whole|complete)|delete (the )?(site|website|database))\b"
)
_ADD_VERB = _rx(r"\b(add|create|build|make|implement|introduce|insert|include|put|need|want)\b")
_CONFIG_VERB = _rx(r"\b(change|update|set|edit|rename|replace|make|switch|use|correct|fix (the )?typo)\b")
_REORDER = _rx(r"\b(move|reorder|re-order|swap|rearrange|put .+ (above|below|before|after))\b")
_REMOVE = _rx(r"\b(remove|delete|hide|take out|get rid of)\b")
_CONTENT_NOUN = _rx(
    r"\b(price|prices|text|heading|headline|title|subtitle|subheading|name|description|image|photo|picture|link|url|"
    r"button|label|copy|wording|quote|author|phone|email address|address|logo|caption)\b"
)
_STYLE_NOUN = _rx(r"\b(colou?r|background|font|spacing|padding|margin|size|theme|align(ment)?|columns?)\b")


@dataclass
class IntentSignals:
    text: str
    interactive: bool = False
    backend: bool = False
    database: bool = False
    destructive: bool = False
    add_verb: bool = False
    config_verb: bool = False
    reorder: bool = False
    remove: bool = False
    content_noun: bool = False
    style_noun: bool = False
    approval_reasons: set[ApprovalReason] = field(default_factory=set)
    matched_terms: list[str] = field(default_factory=list)

    @property
    def capability_request(self) -> bool:
        """The request asks for behaviour, not content."""
        return self.interactive or self.backend or self.database

    @property
    def looks_like_config_edit(self) -> bool:
        return (self.config_verb and (self.content_noun or self.style_noun)) or self.reorder or self.remove


def detect_intent(text: str) -> IntentSignals:
    s = IntentSignals(text=text)
    for name, rx in (
        ("interactive", _INTERACTIVE),
        ("backend", _BACKEND),
        ("database", _DATABASE),
        ("destructive", _DESTRUCTIVE),
    ):
        m = rx.search(text)
        if m:
            setattr(s, name, True)
            s.matched_terms.append(f"{name}:{m.group(0).lower()}")
    s.add_verb = bool(_ADD_VERB.search(text))
    s.config_verb = bool(_CONFIG_VERB.search(text))
    s.reorder = bool(_REORDER.search(text))
    s.remove = bool(_REMOVE.search(text))
    s.content_noun = bool(_CONTENT_NOUN.search(text))
    s.style_noun = bool(_STYLE_NOUN.search(text))
    if s.database:
        s.backend = True
    if s.database:
        s.approval_reasons.add(ApprovalReason.DATABASE_SCHEMA)
    if _AUTH.search(text):
        s.approval_reasons.add(ApprovalReason.AUTHENTICATION)
    if _PAYMENT.search(text):
        s.approval_reasons.add(ApprovalReason.PAYMENTS)
    if _TENANT.search(text):
        s.approval_reasons.add(ApprovalReason.TENANT_ISOLATION)
    return s
