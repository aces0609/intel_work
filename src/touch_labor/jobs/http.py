"""HTTP helper: one session per source (separate cookie jars), spacing, and retries."""
from __future__ import annotations

import time

import requests

UA = "intel_work-research/0.1"
MIN_INTERVAL = 0.25  # seconds between requests to the same source


class Client:
    def __init__(self, min_interval: float = MIN_INTERVAL, retries: int = 4):
        self.retries = retries
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.min_interval = min_interval
        self._last = 0.0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def request(self, method: str, url: str, retries: int | None = None, **kw) -> requests.Response:
        retries = retries or self.retries
        kw.setdefault("timeout", 60)
        err: Exception | None = None
        for attempt in range(retries):
            self._wait()
            try:
                r = self.s.request(method, url, **kw)
                if r.status_code in (429, 444, 500, 502, 503, 504):  # 444 = Walmart's throttle
                    time.sleep(2 ** attempt * 10)
                    err = requests.HTTPError(f"{r.status_code} from {url}")
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                err = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{method} {url} failed after {retries} tries: {err}")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
