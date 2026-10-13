"""Tests for the login rate limiter."""

from __future__ import annotations

import time

from app.core.ratelimit import RateLimiter


def test_locks_after_max_attempts() -> None:
    limiter = RateLimiter(max_attempts=3, window_seconds=60)
    assert limiter.register_failure("ip") is False
    assert limiter.register_failure("ip") is False
    assert limiter.register_failure("ip") is True
    assert limiter.is_locked("ip")


def test_reset_clears_state() -> None:
    limiter = RateLimiter(max_attempts=2, window_seconds=60)
    limiter.register_failure("ip")
    limiter.reset("ip")
    assert not limiter.is_locked("ip")


def test_window_expiry_unlocks() -> None:
    limiter = RateLimiter(max_attempts=1, window_seconds=1)
    limiter.register_failure("ip")
    assert limiter.is_locked("ip")
    time.sleep(1.1)
    assert not limiter.is_locked("ip")


def test_retry_after_positive_when_locked() -> None:
    limiter = RateLimiter(max_attempts=1, window_seconds=30)
    limiter.register_failure("ip")
    assert limiter.retry_after("ip") > 0
