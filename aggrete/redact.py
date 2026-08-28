"""Redact secrets and PII from what an upstream returns, before it reaches the
model. Deterministic and regex-based, so a redaction is reproducible and shows
up in the audit line. Opt-in via ``redact:`` in the proxy config.

Enforcement still runs on the original text (person IDs are extracted for the
policy first); redaction only masks the payload that is handed back to the
assistant. This is the community's "PII/secret masking on the payload path" ask.
"""
from __future__ import annotations

import re

# name -> compiled pattern. Order does not matter; each is applied once.
BUILTIN: dict[str, re.Pattern] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "api_key": re.compile(r"\b(?:sk|pk|rk|ghp|xox[baprs])[-_][A-Za-z0-9]{16,}\b"),
    "bearer": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}\b"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

# Sensible default set; leaves IP out to avoid masking version strings etc.
DEFAULT = ["email", "ssn", "credit_card", "aws_key", "api_key", "bearer"]


def rules_from_config(cfg) -> list[tuple[str, re.Pattern]]:
    """Turn the ``redact:`` config value into a list of (name, pattern).

    ``redact: true`` uses the default set; ``redact: [email, ssn]`` selects
    built-ins by name; a falsey value disables redaction (empty list).
    """
    if not cfg:
        return []
    names = DEFAULT if cfg is True else list(cfg)
    return [(n, BUILTIN[n]) for n in names if n in BUILTIN]


def redact(text: str, rules) -> tuple[str, dict]:
    """Return ``(masked_text, counts)`` where counts maps rule name -> hits."""
    counts: dict[str, int] = {}
    for name, pat in rules:
        def _sub(_m, _n=name):
            counts[_n] = counts.get(_n, 0) + 1
            return f"[redacted:{_n}]"
        text = pat.sub(_sub, text)
    return text, counts
