"""Per-user fixed-window rate limiting."""

from __future__ import annotations

from aggrete import ratelimit
from aggrete.ratelimit import RateLimiter


def test_allows_up_to_max_then_denies():
    rl = RateLimiter(3, 60)
    assert rl.allow("u") == (True, 1)
    assert rl.allow("u") == (True, 2)
    assert rl.allow("u") == (True, 3)
    ok, count = rl.allow("u")
    assert ok is False and count == 4


def test_per_user_isolation():
    rl = RateLimiter(1, 60)
    assert rl.allow("alice")[0] is True
    assert rl.allow("bob")[0] is True
    assert rl.allow("alice")[0] is False


def test_zero_max_disables_the_limit():
    rl = RateLimiter(0, 60)
    for _ in range(5):
        assert rl.allow("u") == (True, 0)


def test_window_resets(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: clock[0])
    rl = RateLimiter(2, 10)
    assert rl.allow("u")[0] is True
    assert rl.allow("u")[0] is True
    assert rl.allow("u")[0] is False   # third call inside the window
    clock[0] += 11                     # window passes
    assert rl.allow("u")[0] is True    # counter has aged out


def test_from_config():
    assert ratelimit.from_config(None) is None
    assert ratelimit.from_config({}) is None
    rl = ratelimit.from_config({"max_calls": 5, "window": "30s"})
    assert rl is not None and rl.max == 5 and rl.window == 30
