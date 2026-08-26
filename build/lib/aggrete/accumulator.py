"""Layer 4 state: what each user has already pulled, across every connector.

Keyed to the *user*, not the session — a new chat must not reset the budget.
MemoryStore is for tests and single-process runs; RedisStore is what you
deploy, because the whole point is state shared across clients and gateways.
"""

from __future__ import annotations

import time
from typing import Iterable, Protocol


def parse_window(w: str | int) -> int:
    if isinstance(w, int):
        return w
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(w[:-1]) * units[w[-1]]


class Store(Protocol):
    def record(self, user: str, domain: str, entities: Iterable[str], ttl: int) -> None: ...
    def entities(self, user: str, domain: str) -> set[str]: ...
    def domains(self, user: str) -> set[str]: ...
    def grant(self, user: str, rule_id: str, ttl: int, purpose: str) -> None: ...
    def granted(self, user: str, rule_id: str) -> str | None: ...
    def reset(self, user: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._ents: dict[tuple[str, str], dict[str, float]] = {}
        self._grants: dict[tuple[str, str], tuple[float, str]] = {}

    def _live(self, key: tuple[str, str]) -> dict[str, float]:
        now = time.time()
        bucket = {e: exp for e, exp in self._ents.get(key, {}).items() if exp > now}
        self._ents[key] = bucket
        return bucket

    def record(self, user, domain, entities, ttl):
        key = (user, domain)
        bucket = self._live(key)
        exp = time.time() + ttl
        # touching a domain with no entities still marks the domain as seen
        bucket.setdefault("__touched__", exp)
        for e in entities:
            bucket[e] = exp
        self._ents[key] = bucket

    def entities(self, user, domain):
        return {e for e in self._live((user, domain)) if e != "__touched__"}

    def domains(self, user):
        return {d for (u, d) in list(self._ents) if u == user and self._live((u, d))}

    def grant(self, user, rule_id, ttl, purpose):
        self._grants[(user, rule_id)] = (time.time() + ttl, purpose)

    def granted(self, user, rule_id):
        exp, purpose = self._grants.get((user, rule_id), (0, ""))
        return purpose if exp > time.time() else None

    def reset(self, user):
        for k in [k for k in self._ents if k[0] == user]:
            del self._ents[k]
        for k in [k for k in self._grants if k[0] == user]:
            del self._grants[k]


class RedisStore:
    """Same contract, backed by Redis sets with TTLs."""

    def __init__(self, client, prefix: str = "coc"):
        self.r = client
        self.p = prefix

    def _key(self, user, domain):
        return f"{self.p}:ents:{user}:{domain}"

    def record(self, user, domain, entities, ttl):
        key = self._key(user, domain)
        pipe = self.r.pipeline()
        pipe.sadd(key, "__touched__", *entities)
        pipe.expire(key, ttl)
        pipe.sadd(f"{self.p}:domains:{user}", domain)
        pipe.expire(f"{self.p}:domains:{user}", ttl)
        pipe.execute()

    def entities(self, user, domain):
        raw = self.r.smembers(self._key(user, domain))
        return {e.decode() if isinstance(e, bytes) else e for e in raw} - {"__touched__"}

    def domains(self, user):
        live = set()
        for d in self.r.smembers(f"{self.p}:domains:{user}"):
            d = d.decode() if isinstance(d, bytes) else d
            if self.r.exists(self._key(user, d)):
                live.add(d)
        return live

    def grant(self, user, rule_id, ttl, purpose):
        self.r.setex(f"{self.p}:grant:{user}:{rule_id}", ttl, purpose)

    def granted(self, user, rule_id):
        v = self.r.get(f"{self.p}:grant:{user}:{rule_id}")
        return (v.decode() if isinstance(v, bytes) else v) if v else None

    def reset(self, user):
        for k in self.r.scan_iter(f"{self.p}:*:{user}*"):
            self.r.delete(k)
