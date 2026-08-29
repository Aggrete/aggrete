"""Compiles coc.yaml into decisions. No model in this path. Deterministic only."""

from __future__ import annotations

import datetime
import os
import time

from dataclasses import dataclass, field
from itertools import combinations

import yaml

from .accumulator import MemoryStore, Store, parse_window


@dataclass
class Decision:
    allow: bool = True
    rule_id: str | None = None
    clause: str | None = None
    owner: str | None = None
    remediation: str | None = None
    evidence: dict = field(default_factory=dict)
    alerts: list[dict] = field(default_factory=list)
    granted_purpose: str | None = None

    def explain(self) -> str:
        if self.allow:
            return "allowed"
        return (
            f"Blocked by {self.rule_id}.\n\n"
            f"{' '.join((self.clause or '').split())}\n\n"
            f"{' '.join((self.remediation or '').split())}\n\n"
            f"Rule owner: {self.owner}"
        )


class Rule:
    def __init__(self, raw: dict, defaults: dict):
        self.id = raw["rule_id"]
        self.clause = raw["clause"]
        self.owner = raw.get("owner", "unassigned")
        self.severity = raw.get("severity", "medium")
        self.remediation = raw.get("remediation", "")
        self.tests = raw.get("tests", [])
        self.pack = raw.get("pack")
        self.enforce = []
        for e in raw.get("enforce", []):
            e = dict(e)
            e["window_s"] = parse_window(e.get("window", defaults.get("window", "24h")))
            self.enforce.append(e)

    def blocks(self, kind: str):
        return [e for e in self.enforce if e.get("type") == kind]


def _ts(value) -> float | None:
    """ISO date or datetime to epoch seconds; None if unset."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.timestamp()
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time()).timestamp()
    return datetime.datetime.fromisoformat(str(value)).timestamp()


def in_scope(e: dict, user: str, now: float | None = None) -> bool:
    """Does this enforcement block apply to this user right now?

    `allowed_users` exempts people (counsel, the consultation team);
    `blocked_users` targets people (the subject of an investigation);
    `since` / `until` bound the rule in time (embargoes, quiet periods).
    """
    now = time.time() if now is None else now
    u = user.strip().lower()
    if e.get("allowed_users") and u in {x.strip().lower() for x in e["allowed_users"]}:
        return False
    if e.get("blocked_users") and u not in {x.strip().lower() for x in e["blocked_users"]}:
        return False
    since, until = _ts(e.get("since")), _ts(e.get("until"))
    if since and now < since:
        return False
    if until and now >= until:
        return False
    return True


class Engine:
    """Evaluates Layer 3 (this call) and Layer 4 (everything so far)."""

    def __init__(self, coc_path: str, store: Store | None = None, pack_state_path: str | None = None):
        doc = yaml.safe_load(open(coc_path))
        self.defaults = doc.get("defaults", {})
        self.rules = [Rule(r, self.defaults) for r in doc["rules"]]
        self.store = store or MemoryStore()
        self.pack_meta = doc.get("packs", [])
        self._pack_default = {p["id"]: bool(p.get("enabled", True)) for p in self.pack_meta}
        self.pack_state_path = pack_state_path
        self._overrides: dict[str, bool] = {}
        self._overrides_mtime = 0.0
        self._load_overrides()

    def _load_overrides(self) -> None:
        """Pack on/off can be toggled from the console via a small JSON file the
        proxy re-reads lazily, so a toggle takes effect without a restart."""
        if not self.pack_state_path:
            return
        try:
            mtime = os.path.getmtime(self.pack_state_path)
        except OSError:
            self._overrides = {}
            return
        if mtime == self._overrides_mtime:
            return
        try:
            import json
            self._overrides = {k: bool(v) for k, v in json.load(open(self.pack_state_path)).items()}
            self._overrides_mtime = mtime
        except (OSError, ValueError):
            self._overrides = {}

    def pack_enabled(self, pack_id: str | None) -> bool:
        if not pack_id:
            return True
        self._load_overrides()
        if pack_id in self._overrides:
            return self._overrides[pack_id]
        return self._pack_default.get(pack_id, True)

    def _active(self, rule) -> bool:
        return self.pack_enabled(rule.pack)

    def packs(self) -> list[dict]:
        """Pack metadata plus live enabled state and rule counts, for the console."""
        counts: dict[str, int] = {}
        for r in self.rules:
            if r.pack:
                counts[r.pack] = counts.get(r.pack, 0) + 1
        out = []
        for p in self.pack_meta:
            out.append({**p, "enabled": self.pack_enabled(p["id"]), "rules": counts.get(p["id"], 0)})
        return out

    def set_pack(self, pack_id: str, enabled: bool) -> None:
        import json
        self._load_overrides()
        state = dict(self._overrides)
        state[pack_id] = bool(enabled)
        if self.pack_state_path:
            with open(self.pack_state_path, "w") as f:
                json.dump(state, f)
        self._overrides = state
        try:
            self._overrides_mtime = os.path.getmtime(self.pack_state_path) if self.pack_state_path else 0.0
        except OSError:
            pass

    # ---------- before the upstream call ----------

    def pre_call(self, user: str, domain: str) -> Decision:
        """Deny before fetching where we already know enough to decide.

        This is the difference between blocking a leak and merely logging one:
        data that is never retrieved cannot be redacted imperfectly.
        """
        for rule in self.rules:
            if not self._active(rule):
                continue
            for e in rule.blocks("flow"):
                # Prompt-injection shield: once a session has read untrusted
                # content, it may not reach an egress-capable domain.
                if domain not in e.get("egress_domains", []) or not in_scope(e, user):
                    continue
                if e.get("action", "deny") != "deny":
                    continue
                tainted = [t for t in e.get("taint_domains", []) if t in self.store.domains(user)]
                if tainted:
                    if purpose := self.store.granted(user, rule.id):
                        return Decision(allow=True, rule_id=rule.id, granted_purpose=purpose)
                    return self._deny(rule, {"egress": domain, "tainted_by": tainted})
            for e in rule.blocks("domain_block"):
                if domain in e["domains"] and e.get("action", "deny") == "deny" and in_scope(e, user):
                    return self._deny(rule, {"domain": domain})

            for e in rule.blocks("wall"):
                # Embargoes, investigation walls, privilege: who may reach a domain, and until when.
                if domain not in e["domains"] or not in_scope(e, user):
                    continue
                if e.get("action", "deny") != "deny":
                    continue
                if purpose := self.store.granted(user, rule.id):
                    return Decision(allow=True, rule_id=rule.id, granted_purpose=purpose)
                return self._deny(rule, {"domain": domain, "until": e.get("until"),
                                         "allowed_users": e.get("allowed_users"), "blocked_users": e.get("blocked_users")})

            for e in rule.blocks("domain_join"):
                if domain not in e["domains"] or not in_scope(e, user):
                    continue
                already = [d for d in e["domains"] if d != domain and d in self.store.domains(user)]
                if len(already) != len(e["domains"]) - 1:
                    continue  # this call would not complete the set
                if e.get("require_entity_overlap", True):
                    overlap = set.intersection(*[self.store.entities(user, d) for d in already])
                    if not overlap:
                        continue
                else:
                    overlap = set()
                if e.get("action", "deny") != "deny":
                    continue
                if purpose := self.store.granted(user, rule.id):
                    return Decision(allow=True, rule_id=rule.id, granted_purpose=purpose)
                return self._deny(
                    rule,
                    {"completes": e["domains"], "already_held": already,
                     "shared_entities": sorted(overlap)[:10]},
                )
        return Decision(allow=True)

    def tool_visible(self, user: str, domain: str | None) -> bool:
        """Whether a tool in `domain` should even be listed for `user`.

        Static gates hide tools from people who could never call them: a
        `domain_block` domain, or a `wall` the user is not exempt from and has
        no granted purpose for. Accumulation rules (`domain_join`) do not hide
        anything, because the tool is fine until the forbidden set is completed.
        What is never listed is never called.
        """
        if not domain:
            return True
        for rule in self.rules:
            if not self._active(rule):
                continue
            for e in rule.blocks("domain_block"):
                if domain in e["domains"] and e.get("action", "deny") == "deny" and in_scope(e, user):
                    return False
            for e in rule.blocks("wall"):
                if domain in e["domains"] and in_scope(e, user) and e.get("action", "deny") == "deny":
                    if not self.store.granted(user, rule.id):
                        return False
        return True

    # ---------- after the upstream call ----------

    def post_call(self, user: str, domain: str, entities: list[str]) -> Decision:
        """Record what came back, then re-evaluate. Denials redact the result."""
        ttl = max((e["window_s"] for r in self.rules for e in r.enforce), default=86400)
        self.store.record(user, domain, entities, ttl)

        alerts: list[dict] = []
        for rule in self.rules:
            if not self._active(rule):
                continue
            for e in rule.blocks("min_group"):
                # Aggregate-only answers: a result about fewer than k people is one person's data.
                if e["domain"] != domain or not in_scope(e, user):
                    continue
                n = len(set(entities))
                if 0 < n < int(e.get("k", 10)):
                    hit = {"rule_id": rule.id, "domain": domain, "people": n, "k": int(e.get("k", 10))}
                    if e.get("action", "alert") == "deny" and not self.store.granted(user, rule.id):
                        return self._deny(rule, hit)
                    alerts.append(hit)

            for e in rule.blocks("entity_budget"):
                if e["domain"] != domain:
                    continue
                count = len(self.store.entities(user, e["domain"]))
                if count > e["max_distinct"]:
                    hit = {"rule_id": rule.id, "domain": e["domain"],
                           "distinct": count, "max": e["max_distinct"]}
                    if e.get("action", "alert") == "deny" and not self.store.granted(user, rule.id):
                        return self._deny(rule, hit)
                    alerts.append(hit)

            for e in rule.blocks("self_comparison"):
                # The requester's own record next to colleagues' records in the
                # same domain: the precondition for any "how do I compare" answer.
                if e["domain"] != domain:
                    continue
                seen = self.store.entities(user, domain)
                me = f"p:{user.strip().lower()}"
                others = sorted(x for x in seen if x != me)
                if me in seen and others:
                    hit = {"rule_id": rule.id, "domain": domain, "self": me,
                           "others": others[:10], "distinct_others": len(others)}
                    if e.get("action", "alert") == "deny" and not self.store.granted(user, rule.id):
                        return self._deny(rule, hit)
                    alerts.append(hit)

            for e in rule.blocks("domain_join"):
                if domain not in e["domains"] or not in_scope(e, user):
                    continue
                if not set(e["domains"]) <= self.store.domains(user):
                    continue
                overlap = set.intersection(*[self.store.entities(user, d) for d in e["domains"]])
                if e.get("require_entity_overlap", True) and not overlap:
                    continue
                if e.get("action", "deny") != "deny":
                    alerts.append({"rule_id": rule.id, "overlap": sorted(overlap)[:10]})
                    continue
                if purpose := self.store.granted(user, rule.id):
                    alerts.append({"rule_id": rule.id, "granted_purpose": purpose})
                    continue
                return self._deny(rule, {"domains": e["domains"],
                                         "shared_entities": sorted(overlap)[:10]}, alerts)

        return Decision(allow=True, alerts=alerts)

    # ---------- purpose binding ----------

    def grant_purpose(self, user: str, rule_id: str, purpose: str, ttl_s: int = 4 * 3600):
        """The escape valve. Without a workable one, users route around the system."""
        self.store.grant(user, rule_id, ttl_s, purpose)

    def _deny(self, rule: Rule, evidence: dict, alerts: list | None = None) -> Decision:
        return Decision(False, rule.id, rule.clause, rule.owner, rule.remediation,
                        evidence, alerts or [])
