"""Handling of untrusted input (client prompts, uploaded content, LLM output).

Security rules are enforced in code, not in prompts. This module only *flags*
suspicious content so the plan can record it; the flags never relax a rule.
"""

from __future__ import annotations

import re

_INJECTION = [
    (re.compile(r"(?i)\bignore (all |any )?(previous|prior|above) (instructions|rules)"), "prompt_injection"),
    (re.compile(r"(?i)\b(disregard|bypass|override) (the |your |all )?(rules|policy|policies|security)"), "prompt_injection"),
    (re.compile(r"(?i)\byou are now\b|\bsystem prompt\b|\bdeveloper mode\b"), "prompt_injection"),
    (
        re.compile(
            r"(?i)\b(show|print|read|reveal|expose|give|send|output|dump|leak|access|return|log)\b.{0,40}"
            r"(\.env\b|environment variables?|api[_ ]?keys?|access tokens?|github[_ ]?token|secrets\b|credentials|private keys?)"
        ),
        "secret_access_attempt",
    ),
    (
        re.compile(
            r"(?i)\b(access|read|show|copy|view|modify|change|delete|edit|use)\b.{0,40}"
            r"\b(another|other|different)\s+(tenant|client|customer|account)s?'?s?\s+(site|website|data|repo|repository|config|configuration|project|files?)"
        ),
        "cross_tenant_reference",
    ),
    (re.compile(r"(?i)\b(rm -rf|curl\s+http|wget\s+http|child_process|eval\()"), "command_execution_attempt"),
]


def scan_untrusted_text(text: str) -> list[str]:
    flags: list[str] = []
    for pattern, flag in _INJECTION:
        if pattern.search(text) and flag not in flags:
            flags.append(flag)
    return flags


# Flags that make the request impossible to honour safely: the job is rejected.
BLOCKING_FLAGS = frozenset({"secret_access_attempt", "cross_tenant_reference", "command_execution_attempt"})


def fence(label: str, content: str) -> str:
    """Wrap untrusted content for an LLM prompt so it is clearly data, not instructions."""
    safe = content.replace("</untrusted", "<\\/untrusted")
    return f'<untrusted source="{label}">\n{safe}\n</untrusted>'
