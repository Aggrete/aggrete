"""Per-user call rate limiting: a ceiling on tool calls per time window.

Aggrete's accumulator bounds *what* a user can assemble over time; this bounds
*how fast* they can call at all, which the accumulator does not. It is a blunt
denial-of-wallet and abuse control, deterministic like everything else here.

Fixed window, not sliding: calls are counted in `window` buckets and the count
resets at each boundary. That is coarse but cheap and atomic in Redis, which is
what matters for a limiter. Backed by Redis when the proxy is (so the limit is
shared across replicas), else in-process.
"""

from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, window_s: int, redis_client=None, prefix: str = "aggrete:rl"):
        self.max = int(max_calls)
        self.window = int(window_s)
        self.r = redis_client
        self.prefix = prefix
        self._mem: dict[str, deque[float]] = {}

    def allow(self, user: str) -> tuple[bool, int]:
        """Record a call for `user` and say whether it is within the limit.

        Returns (allowed, count_in_window). When not allowed the call is still
        counted, so a client that keeps hammering stays over the line rather than
        slipping through on the reset it triggered.
        """
        if self.max <= 0:
            return True, 0
        if self.r is not None:
            return self._allow_redis(user)
        return self._allow_memory(user)

    def _allow_memory(self, user: str) -> tuple[bool, int]:
        now = time.time()
        q = self._mem.setdefault(user, deque())
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        q.append(now)
        return len(q) <= self.max, len(q)

    def _allow_redis(self, user: str) -> tuple[bool, int]:
        bucket = int(time.time() // self.window)
        key = f"{self.prefix}:{user}:{bucket}"
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, self.window * 2)
        count = int(pipe.execute()[0])
        return count <= self.max, count


def from_config(cfg: dict | None, redis_client=None) -> RateLimiter | None:
    """Build a limiter from a `rate_limit:` block, or None when unset.

    `max_calls` and `window` (e.g. '1m', '30s', '1h') are required; window
    accepts the same suffixes as policy windows.
    """
    if not cfg or not cfg.get("max_calls"):
        return None
    from .accumulator import parse_window
    return RateLimiter(cfg["max_calls"], parse_window(cfg.get("window", "1m")), redis_client)
