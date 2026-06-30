"""
Retry decorator using tenacity with exponential back-off.

Retryable conditions:
  - HTTP 429 (Too Many Requests)
  - HTTP 5xx (500, 502, 503, 504)
  - requests.Timeout
  - requests.ConnectionError

Non-retryable conditions:
  - HTTP 4xx (except 429) — caller bug; retrying won't help

429 responses use a much longer backoff (30 s → 60 s → 120 s) because
Expensify's rate-limit window typically spans several tens of seconds.
Waiting only 2 s after a 429 causes the very next attempt to hit the limit
again.  Transient errors (5xx, timeouts) keep the standard short backoff.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
)

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

# ---------------------------------------------------------------------------
# Retry predicates
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    """Return True if *exc* warrants a retry."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status in _RETRYABLE_STATUS_CODES
    return False


# ---------------------------------------------------------------------------
# Wait strategy: long backoff for 429, short for everything else
# ---------------------------------------------------------------------------

#: Minimum wait after a 429 rate-limit response (seconds).
WAIT_MIN_RATE_LIMIT = 30.0

#: Maximum wait after a 429 rate-limit response (seconds).
WAIT_MAX_RATE_LIMIT = 120.0

#: Minimum wait after a transient server error (seconds).
WAIT_MIN_TRANSIENT = 2.0

#: Maximum wait after a transient server error (seconds).
WAIT_MAX_TRANSIENT = 60.0


def _compute_wait(retry_state) -> float:  # type: ignore[return]
    """Return wait time in seconds based on the exception that triggered retry.

    HTTP 429 → starts at 30 s, doubles each attempt, caps at 120 s.
    Everything else → starts at 2 s, doubles each attempt, caps at 60 s.
    """
    exc = retry_state.outcome.exception()
    attempt = retry_state.attempt_number  # 1 on first retry

    is_429 = (
        isinstance(exc, requests.HTTPError)
        and getattr(getattr(exc, "response", None), "status_code", None) == 429
    )

    if is_429:
        seconds = WAIT_MIN_RATE_LIMIT * (2 ** (attempt - 1))
        return min(seconds, WAIT_MAX_RATE_LIMIT)

    seconds = WAIT_MIN_TRANSIENT * (2 ** (attempt - 1))
    return min(seconds, WAIT_MAX_TRANSIENT)


# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

#: Maximum number of retry attempts (4 retries = 5 total calls).
MAX_RETRIES = 4

retryable_request = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(MAX_RETRIES + 1),  # tenacity counts total attempts
    wait=_compute_wait,
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
"""Decorator that applies exponential back-off retry logic.

Apply to any function that makes HTTP requests::

    @retryable_request
    def fetch_report(self, params):
        response = self._session.post(...)
        response.raise_for_status()
        return response
"""
