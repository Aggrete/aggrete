"""Pull stable person identifiers out of whatever a connector hands back.

This function is where this whole design succeeds or fails. Names are not
identifiers. Prefer source-system IDs, then email, and treat free text as a
last resort. Tune `IDENTIFIER_KEYS` against your own connectors before trusting
any threshold you set in coc.yaml.

Linking: one JSON object that carries several identifier fields (e.g. both
`email` and `employee_id`) is ONE person and yields ONE canonical key. Email is
preferred as the canonical form because it is the identifier most likely to be
shared across connectors. Cross-domain overlap (COC-HR-004) only works when
both sides produce the same key. When a record has no email the first ID field
found (in `ID_KEYS` order) is used instead.
"""

from __future__ import annotations

import json
import re

EMAIL_KEYS = ("email", "user_email", "primary_email", "mail", "owner_email")
ID_KEYS = ("employee_id", "person_id", "worker_id", "user_id", "assignee_id",
           "owner_id", "sfid", "slack_user_id")
IDENTIFIER_KEYS = set(EMAIL_KEYS) | set(ID_KEYS)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def normalize(value: str) -> str:
    v = str(value).strip().lower()
    return f"p:{v}"


def _canonical(record: dict) -> str | None:
    """Return the single canonical key for a record, or None if it has none."""
    lowered = {k.lower(): v for k, v in record.items() if isinstance(v, (str, int))}
    for k in EMAIL_KEYS:
        if k in lowered and str(lowered[k]).strip():
            return normalize(lowered[k])
    for k in ID_KEYS:
        if k in lowered and str(lowered[k]).strip():
            return normalize(lowered[k])
    return None


def from_json(obj, out: set[str]) -> set[str]:
    if isinstance(obj, dict):
        key = _canonical(obj)
        if key:
            out.add(key)
        # Nested objects may describe other people (e.g. "manager": {...}).
        for k, v in obj.items():
            if k.lower() not in IDENTIFIER_KEYS:
                from_json(v, out)
    elif isinstance(obj, list):
        for item in obj:
            from_json(item, out)
    return out


def extract(text: str) -> list[str]:
    """Best-effort extraction from one tool result payload."""
    found: set[str] = set()
    try:
        from_json(json.loads(text), found)
    except (ValueError, TypeError):
        pass
    if not found:  # fall back to emails in prose
        found |= {normalize(m) for m in EMAIL_RE.findall(text or "")}
    return sorted(found)
