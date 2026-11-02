"""HTTP with exponential backoff, plus a per-source rate limiter."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RateLimiter:
    def __init__(self, min_interval_s: float):
        self.min_interval = max(0.0, min_interval_s)
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    attempts: int = 4,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Request and decode JSON. Retries 429/5xx/transport errors with backoff (2s, 4s, 8s…, jittered)."""
    last_exc: Exception | None = None
    for i in range(attempts):
        if limiter:
            limiter.wait()
        try:
            r = client.request(method, url, **kwargs)
            if r.status_code in RETRY_STATUS:
                raise httpx.HTTPStatusError(f"retryable {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in RETRY_STATUS:
                raise
            last_exc = e
            retry_after = e.response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else base_delay * 2**i
        except httpx.TransportError as e:
            last_exc = e
            delay = base_delay * 2**i
        if i < attempts - 1:
            delay = min(delay, 60) * (0.8 + 0.4 * random.random())
            log.warning("retrying request", extra={"url": url, "attempt": i + 1, "delay_s": round(delay, 1)})
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
