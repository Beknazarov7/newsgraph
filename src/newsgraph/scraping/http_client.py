"""Polite, retrying HTTP client.

Design notes:
- One client instance per pipeline run; the rate limiter is per-host because
  that's the unit a site cares about (a single TechCrunch fetch shouldn't
  delay an unrelated host).
- We back off on 429/5xx but NOT on 404 — that's a permanent failure for that
  URL and retrying just slows us down.
- The on-disk cache lives in `cache_dir` keyed by sha1(url). It exists to make
  evals deterministic and to avoid hammering TechCrunch on rerun. The HTTP
  layer is the right place for it (not the parser); the parser should always
  see HTML.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class RetryableHTTPError(Exception):
    """Raised for 429/5xx so tenacity will retry."""


class HTTPClient:
    def __init__(
        self,
        user_agent: str,
        rate_limit_s: float,
        timeout_s: float,
        max_retries: int,
        cache_dir: Path | None = None,
    ):
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout_s,
            follow_redirects=True,
        )
        self._rate_limit_s = rate_limit_s
        self._max_retries = max_retries
        self._cache_dir = cache_dir
        self._last_fetch_per_host: dict[str, float] = {}
        self._lock = threading.Lock()
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HTTPClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---- cache --------------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{key}.html"

    def _cached(self, url: str) -> str | None:
        p = self._cache_path(url)
        if p is not None and p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def _store(self, url: str, body: str) -> None:
        p = self._cache_path(url)
        if p is not None:
            p.write_text(body, encoding="utf-8")

    # ---- politeness ---------------------------------------------------------

    def _throttle(self, host: str) -> None:
        with self._lock:
            last = self._last_fetch_per_host.get(host, 0.0)
            wait = self._rate_limit_s - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_fetch_per_host[host] = time.monotonic()

    # ---- fetch --------------------------------------------------------------

    def get(self, url: str, *, use_cache: bool = True) -> str:
        if use_cache:
            cached = self._cached(url)
            if cached is not None:
                logger.debug("cache hit %s", url)
                return cached

        host = httpx.URL(url).host

        def _do() -> str:
            return self._fetch_with_retry(url, host)

        body = _do()
        self._store(url, body)
        return body

    def _fetch_with_retry(self, url: str, host: str) -> str:
        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((RetryableHTTPError, httpx.TransportError)),
        )
        def _attempt() -> str:
            self._throttle(host)
            resp = self._client.get(url)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                raise RetryableHTTPError(f"{resp.status_code} on {url}")
            resp.raise_for_status()
            return resp.text

        return _attempt()
