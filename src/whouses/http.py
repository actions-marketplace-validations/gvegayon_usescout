"""Shared HTTP client with a polite User-Agent, retries on 429/5xx, and an injectable sleeper."""
from __future__ import annotations

import time

import httpx

UA = "whouses/0.1 (+https://github.com/gvegayon/whouses)"


class Http:
    def __init__(self, client: httpx.Client | None = None, sleep=time.sleep, retries: int = 3):
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
        self.sleep = sleep
        self.retries = retries

    def get(self, url: str, **kw) -> httpx.Response:
        last = None
        for attempt in range(self.retries):
            r = self.client.get(url, **kw)
            if r.status_code in (429, 502, 503, 504) or (
                r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"
            ):
                wait = int(r.headers.get("retry-after", 2 ** (attempt + 2)))
                self.sleep(min(wait, 90))
                last = r
                continue
            return r
        return last  # type: ignore[return-value]

    def json(self, url: str, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()
