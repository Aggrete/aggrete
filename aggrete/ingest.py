"""Turn a code-of-conduct document (PDF, DOCX, Markdown, text) into coc.yaml.

    python -m aggrete.ingest handbook.pdf --domains proxy.config.yaml -o coc.draft.yaml

The model reads the document and proposes rules in the exact shape coc.yaml
uses. The result is a DRAFT: every rule comes back as `action: alert`, every
rule must pass its own embedded tests through the real Engine before the file
is written, and the clause owner still has to review it. What the model does
here is the part that needs judgment. Recognising that "may not be combined
to derive" is a domain_join over three domains. Enforcement stays deterministic.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

MODEL = os.environ.get("AGGRETE_INGEST_MODEL", "claude-opus-5")  # any model the Anthropic SDK can call

RULE_TYPES = """
Rule types the engine implements (nothing else is enforceable):
- domain_join: deny/alert when one user has touched ALL listed `domains` within
  `window`, optionally only if the same people appear in each
  (`require_entity_overlap: true`). Use for "X, Y and Z may not be combined".
- entity_budget: alert/deny when a user has seen more than `max_distinct`
  people from one `domain` within `window`. Use for "no rosters / bulk lists".
- domain_block: deny any access to the listed `domains`, before fetching.
  Use for "never available to assistants" (legal hold, board materials).
- self_comparison: alert/deny when a user's own record AND colleagues' records
  from one `domain` have both been seen within `window`. Use for "may not be
  used to compare or benchmark individuals" (timesheets, performance, pay).
  In tests, `p:self` stands for the requesting user.
- min_group: alert/deny when a result from `domain` covers fewer than `k`
  people. Use for "only averages / aggregates may be shared" (pay transparency).
- wall: deny access to `domains` for everyone except `allowed_users`, or only
  for `blocked_users`, optionally `until` a date. Use for privilege, embargoes,
  investigation subjects, quiet periods. A test may set `user:` to the identity.
- domain_join/domain_block also accept allowed_users, blocked_users, since, until.
Clauses that need none of these. Tone, harassment, expense etiquette. Are
not enforceable at a data proxy: return them in `unenforceable` with a reason.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "clause": {"type": "string"},
                    "owner": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "remediation": {"type": "string"},
                    "enforce": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layer": {"type": "string", "enum": ["retrieval", "accumulation"]},
                                "action": {"type": "string", "enum": ["alert", "deny"]},
                                "type": {"type": "string", "enum": ["domain_join", "entity_budget", "domain_block", "self_comparison", "min_group", "wall"]},
                                "domains": {"type": "array", "items": {"type": "string"}},
                                "domain": {"type": "string"},
                                "require_entity_overlap": {"type": "boolean"},
                                "max_distinct": {"type": "integer"},
                                "scope": {"type": "string", "enum": ["user"]},
                                "window": {"type": "string"},
                                "k": {"type": "integer"},
                                "allowed_users": {"type": "array", "items": {"type": "string"}},
                                "blocked_users": {"type": "array", "items": {"type": "string"}},
                                "until": {"type": "string"},
                                "since": {"type": "string"},
                            },
                            "required": ["layer", "action", "type", "scope", "window"],
                            "additionalProperties": False,
                        },
                    },
                    "tests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "expect": {"type": "string", "enum": ["allow", "alert", "deny"]},
                                "sequence": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "domain": {"type": "string"},
                                            "entities": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["domain", "entities"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["name", "expect", "sequence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["rule_id", "clause", "owner", "severity", "remediation", "enforce", "tests"],
                "additionalProperties": False,
            },
        },
        "unenforceable": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"clause": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["clause", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rules", "unenforceable"],
    "additionalProperties": False,
}


# --- document loading --------------------------------------------------------

def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def document_block(path: Path) -> dict:
    """Content block for the user turn. PDFs go in natively; the rest as text."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                "title": path.name}
    text = docx_text(path) if suffix == ".docx" else path.read_text(errors="replace")
    return {"type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": text},
            "title": path.name}


def known_domains(config_path: Path | None) -> list[str]:
    if not config_path:
        return []
    cfg = yaml.safe_load(config_path.read_text())
    return sorted(set(cfg.get("domains", {}).values()))


# --- model call ----------------------------------------------------------------

def draft(path: Path, domains: list[str]) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    system = (
        "You convert a company code of conduct into machine-enforceable rules for a "
        "data-access proxy that sits between an AI assistant and internal systems. "
        "The proxy sees which *domain* each tool call touches and which *people* each "
        "result mentions, per user, over a time window. It cannot read intent.\n"
        + RULE_TYPES +
        "\nDomains already wired into the proxy: "
        + (", ".join(domains) if domains else "none yet. Invent short kebab-case names")
        + ". Prefer existing names; introduce new ones only when a clause needs them.\n"
        "Keep clause text verbatim from the document. Give every rule at least one test "
        "that expects the enforced action and one that expects allow; entity IDs in tests "
        "look like p:alice. Rule IDs follow COC-<AREA>-<NNN>. Every action must be `alert`; "
        "a human flips it to `deny` after tuning."
    )
    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": [
            document_block(path),
            {"type": "text", "text": "Extract every enforceable rule from this document."},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise SystemExit(f"model declined: {response.stop_details}")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# --- verification ----------------------------------------------------------------

def verify(coc: dict) -> list[str]:
    """Run each rule's embedded tests through the real Engine. Returns failures."""
    from .accumulator import MemoryStore
    from .policy import Engine

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(coc, f, sort_keys=False)
        tmp = f.name
    failures = []
    for rule in Engine(tmp, MemoryStore()).rules:
        expects = {t["expect"] for t in rule.tests}
        if "allow" not in expects or not (expects & {"alert", "deny"}):
            failures.append(f"{rule.id}: needs both an allow test and an alert/deny test")
        for t in rule.tests:
            engine, user, outcome = Engine(tmp, MemoryStore()), t.get("user", "t@example.com"), "allow"
            for _p in engine.pack_meta:        # a rule's tests validate regardless of its pack's default state
                engine.set_pack(_p["id"], True)
            for step in t["sequence"]:
                if not engine.pre_call(user, step["domain"]).allow:
                    outcome = "deny"; break
                post = engine.post_call(user, step["domain"], [f"p:{user}" if x == "p:self" else x for x in step.get("entities", [])])
                if not post.allow:
                    outcome = "deny"; break
                if post.alerts:
                    outcome = "alert"
            if outcome != t["expect"]:
                failures.append(f"{rule.id}/{t['name']}: expected {t['expect']}, got {outcome}")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("document", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("coc.draft.yaml"))
    ap.add_argument("--domains", type=Path, help="proxy.config.yaml, to reuse its domain names")
    args = ap.parse_args()

    result = draft(args.document, known_domains(args.domains))
    for r in result["rules"]:
        for e in r["enforce"]:
            e["action"] = "alert"  # drafts never deny, whatever the model said
    coc = {"version": 1, "defaults": {"window": "24h", "entity_kind": "person"},
           "rules": result["rules"]}

    failures = verify(coc)
    if failures:
        print("Draft did not pass its own tests; not written:", file=sys.stderr)
        for f in failures:
            print("  -", f, file=sys.stderr)
        raise SystemExit(1)

    header = (f"# Drafted by aggrete.ingest from {args.document.name}. Every action is "
              "`alert`.\n# Review with the clause owner, then flip to `deny` per rule.\n\n")
    args.output.write_text(header + yaml.safe_dump(coc, sort_keys=False, width=88))
    print(f"wrote {args.output}. {len(result['rules'])} rules")
    for u in result["unenforceable"]:
        print(f"  not enforceable here: {u['clause'][:70]}… ({u['reason']})")


if __name__ == "__main__":
    main()
