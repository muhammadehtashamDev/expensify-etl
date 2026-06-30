"""
Production-grade token-bucket rate limiter.

Enforces two independent sliding-window limits for the Expensify API.
The defaults (3/10 s, 12/60 s) are intentionally below Expensify's
documented maximums to avoid 429 responses entirely.  With 3 templates ×
2 HTTP calls per month the pipeline fires 6 requests per month, so a
3-per-10-second burst cap spreads those calls across ~20 seconds and keeps
the 60-second total well inside the long-window budget.

Thread-safety: guarded by a :class:`threading.Lock`, safe for multi-threaded
consumers (though the current pipeline is single-threaded).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from scripts.logger import get_logger

log = get_logger(__name__)


@dataclass
class _Window:
    """A single sliding-window rate-limit constraint."""

    max_requests: int
    window_seconds: float
    _timestamps: Deque[float] = field(default_factory=deque, init=False, repr=False)

    def wait_time(self, now: float) -> float:
        """Return seconds to sleep before the next request is allowed."""
        # Drop timestamps that have fallen outside the window
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

        if len(self._timestamps) < self.max_requests:
            return 0.0

        # The oldest timestamp in the window is the one that will expire next
        oldest = self._timestamps[0]
        return (oldest + self.window_seconds) - now

    def record(self, now: float) -> None:
        """Record that a request was made at *now*."""
        self._timestamps.append(now)


class RateLimiter:
    """Thread-safe dual-window rate limiter for the Expensify API.

    Args:
        short_requests: Max requests in the short window (default 5).
        short_window:   Length of the short window in seconds (default 10).
        long_requests:  Max requests in the long window (default 20).
        long_window:    Length of the long window in seconds (default 60).
    """

    def __init__(
        self,
        short_requests: int = 3,
        short_window: float = 10.0,
        long_requests: int = 12,
        long_window: float = 60.0,
    ) -> None:
        self._lock = threading.Lock()
        self._windows = [
            _Window(max_requests=short_requests, window_seconds=short_window),
            _Window(max_requests=long_requests, window_seconds=long_window),
        ]

    def acquire(self) -> None:
        """Block until a request is permitted by all rate-limit windows.

        Logs every sleep so operators can see back-pressure in the logs.
        """
        with self._lock:
            while True:
                now = time.monotonic()
                delays = [w.wait_time(now) for w in self._windows]
                sleep_for = max(delays)

                if sleep_for <= 0:
                    break

                log.info(
                    "Rate limit back-pressure: sleeping %.2fs before next request.",
                    sleep_for,
                )
                # Release lock while sleeping so other threads can check too
                self._lock.release()
                try:
                    time.sleep(sleep_for)
                finally:
                    self._lock.acquire()

            now = time.monotonic()
            for window in self._windows:
                window.record(now)
