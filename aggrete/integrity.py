"""Tool integrity: catch a connector that changes its tools underneath you.

Two MCP-specific attacks this defends against, both deterministically (no model):

- **Rug pull.** A server advertises a benign tool, you approve it, then later
  swaps in a different description or schema. We fingerprint every tool the first
  time we see it (trust on first use) and flag any later change against the pin.
- **Tool poisoning.** A tool description carries hidden instructions aimed at the
  assistant ("ignore your previous instructions", "always call ... first"). We
  scan descriptions for those patterns.

A change or a poisoned description is either alerted (listed, but recorded in the
audit) or blocked (the tool is hidden and refused), per `on_change` / `on_poison`.
Pins persist to a JSON file so the check survives restarts.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# Phrases that have no business in a tool description and are the signature of a
# prompt-injection or tool-poisoning payload. Matched case-insensitively.
POISON_PATTERNS = [
    r"ignore\s+(all\s+|your\s+|the\s+)?previous\s+instructions",
    r"disregard\s+(all\s+|your\s+|the\s+)?(previous|prior|above)",
    r"do\s+not\s+(tell|mention|inform)\s+the\s+user",
    r"without\s+(telling|informing|asking)\s+the\s+user",
    r"</?(system|assistant|instructions?)>",       # fake role tags
    r"\bsystem\s*:\s*you\s+(are|must|should)",
    r"exfiltrat|send\s+.*\bto\s+https?://",         # data-egress directives
    r"\b(api[_-]?key|password|secret|token|credential)s?\b.*\b(include|append|attach|send)",
    r"always\s+(call|use|invoke)\s+\w+\s+(first|before)",
]
_POISON_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in POISON_PATTERNS]


def fingerprint(name: str, description: str | None, input_schema: dict | None) -> str:
    """A stable hash of everything about a tool that could carry an instruction
    or widen its reach: its name, its description, and its parameter schema."""
    payload = json.dumps(
        {"name": name, "description": description or "",
         "schema": input_schema or {}},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def scan_poison(description: str | None) -> list[str]:
    """Return the poison patterns that match this description (empty if clean)."""
    text = description or ""
    return [p.pattern for p, raw in zip(_POISON_RE, POISON_PATTERNS) if p.search(text)]


class PinStore:
    """Trust-on-first-use fingerprints, persisted as JSON `{tool_name: sha256}`."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self._pins: dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                self._pins = {k: str(v) for k, v in json.loads(self.path.read_text()).items()}
            except (OSError, ValueError):
                self._pins = {}

    def check(self, name: str, fp: str) -> str:
        """Classify a tool against its pin: 'new', 'same', or 'changed'.

        A 'new' tool is pinned as a side effect (trust on first use). A 'changed'
        tool is left pinned to its original value so the change keeps firing until
        someone re-pins it deliberately with `repin`.
        """
        prior = self._pins.get(name)
        if prior is None:
            self._pins[name] = fp
            self._save()
            return "new"
        return "same" if prior == fp else "changed"

    def repin(self, name: str, fp: str) -> None:
        """Accept the current fingerprint as the new baseline."""
        self._pins[name] = fp
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._pins, indent=2, sort_keys=True))
        except OSError:
            pass


def evaluate(name, description, input_schema, pins: PinStore, cfg: dict) -> dict | None:
    """Decide what to do about one tool. Returns None when it is clean, or a dict
    {action: 'alert'|'block', reasons: [...], fingerprint} when something is off.

    `cfg` is the `tool_integrity:` block: `on_change` and `on_poison` are each
    'alert' (default) or 'block'; `scan_poison` toggles the description scan.
    """
    fp = fingerprint(name, description, input_schema)
    reasons: list[str] = []
    action = "alert"

    status = pins.check(name, fp)
    if status == "changed":
        reasons.append("tool definition changed since it was first seen (possible rug pull)")
        if cfg.get("on_change", "alert") == "block":
            action = "block"

    if cfg.get("scan_poison", True):
        hits = scan_poison(description)
        if hits:
            reasons.append(f"description matches {len(hits)} poisoning pattern(s)")
            if cfg.get("on_poison", "alert") == "block":
                action = "block"

    if not reasons:
        return None
    return {"action": action, "reasons": reasons, "fingerprint": fp}
