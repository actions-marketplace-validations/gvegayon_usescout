"""Shared HTTP client with a polite User-Agent, retries on 429/5xx, and an injectable sleeper."""
from __future__ import annotations

import time

import httpx

MAX_WAIT = 90   # longest we will wait inside one request; a longer Retry-After is returned to the caller
UA = "usescout/0.1 (+https://github.com/gvegayon/usescout)"


class Http:
    def __init__(self, client: httpx.Client | None = None, sleep=time.sleep, retries: int = 3):
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
        self.sleep = sleep
        self.retries = retries

    @staticmethod
    def _wait(r: httpx.Response, attempt: int) -> int:
        """Seconds to wait before retrying: what the server asks for, else a backoff long enough for search limits."""
        ra = r.headers.get("retry-after", "")
        if ra.isdigit():
            return int(ra)
        reset = r.headers.get("x-ratelimit-reset", "")
        if r.headers.get("x-ratelimit-remaining") == "0" and reset.isdigit():
            return max(int(reset) - int(time.time()), 1)
        return (20, 45, 70)[min(attempt, 2)] if r.status_code in (403, 429) else 2 ** (attempt + 2)

    def get(self, url: str, **kw) -> httpx.Response:
        last = None
        for attempt in range(self.retries):
            r = self.client.get(url, **kw)
            if r.status_code in (429, 502, 503, 504) or (
                r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"
            ):
                wait = self._wait(r, attempt)
                if wait > MAX_WAIT:   # told to stay away for minutes: retrying now would only extend the penalty
                    return r
                self.sleep(wait)
                last = r
                continue
            return r
        return last  # type: ignore[return-value]

    def json(self, url: str, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()
