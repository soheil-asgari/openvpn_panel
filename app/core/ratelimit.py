"""Thread-safe, in-process login rate limiter.

Keyed by a string (usually ``ip`` or ``ip:username``). Window based sliding
counter: once ``max_attempts`` failures occur inside ``window`` seconds the key
is locked until the window expires.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket.failures = [t for t in bucket.failures if t >= cutoff]

    def is_locked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return False
            if bucket.locked_until and bucket.locked_until > now:
                return True
            self._prune(bucket, now)
            if not bucket.failures:
                self._buckets.pop(key, None)
            return False

    def retry_after(self, key: str) -> int:
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket or bucket.locked_until <= now:
                return 0
            return max(1, int(bucket.locked_until - now))

    def register_failure(self, key: str) -> bool:
        """Record a failure. Returns ``True`` when the key becomes locked."""

        now = time.time()
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            self._prune(bucket, now)
            bucket.failures.append(now)
            if len(bucket.failures) >= self.max_attempts:
                bucket.locked_until = now + self.window_seconds
                bucket.failures.clear()
                return True
            return False

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
