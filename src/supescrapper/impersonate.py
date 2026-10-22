"""Level 3 fetch backend: TLS/JA3 browser impersonation + proxy rotation.

Uses ``curl_cffi`` to present an authentic Chrome (or other) TLS/HTTP stack so
targets protected by Cloudflare, DataDome, Akamai, etc. see a real browser
fingerprint rather than the default Python ``requests``/urllib3 one.

This module is import-safe: importing it does not require ``curl_cffi`` to be
installed. The dependency is only needed when a request is actually made, in
which case a clear :class:`DynamicPageError`-style message is raised.
"""
from __future__ import annotations

import itertools
import random
import threading
import time
from typing import Iterator, Sequence

from .exceptions import FetchError, SupescrapperError

DEFAULT_IMPERSONATE = "chrome124"

# Reasonable modern browser targets bundled with curl_cffi. Users can pass any
# value supported by their installed curl_cffi version.
KNOWN_IMPERSONATE_TARGETS = (
    "chrome99", "chrome100", "chrome101", "chrome104", "chrome107",
    "chrome110", "chrome116", "chrome119", "chrome120", "chrome123",
    "chrome124", "chrome131", "chrome136",
    "edge99", "edge101",
    "safari15_3", "safari15_5", "safari17_0", "safari17_2_1",
    "firefox133", "firefox135",
)


class ImpersonateNotInstalledError(SupescrapperError):
    """Raised when curl_cffi is required but not installed."""


def _require_curl_cffi():
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via patch
        raise ImpersonateNotInstalledError(
            "curl_cffi is required for impersonation/anti-bot fetching. "
            "Install it: pip install supescrapper[impersonate]"
        ) from exc
    return curl_requests


class ProxyPool:
    """Round-robin / random proxy pool with optional per-proxy health tracking.

    Proxies are plain strings accepted by the HTTP backend, e.g.
    ``"http://user:pass@host:port"``.
    """

    def __init__(self, proxies: Sequence[str] | None = None, *, strategy: str = "round_robin"):
        if strategy not in ("round_robin", "random"):
            raise ValueError("strategy must be 'round_robin' or 'random'")
        self._proxies: list[str] = [p for p in (proxies or []) if p]
        self._strategy = strategy
        self._cycle: Iterator[str] | None = itertools.cycle(self._proxies) if self._proxies else None
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    def __bool__(self) -> bool:
        return bool(self._proxies)

    def next(self) -> str | None:
        if not self._proxies:
            return None
        with self._lock:
            if self._strategy == "random":
                return random.choice(self._proxies)
            assert self._cycle is not None
            return next(self._cycle)


class ImpersonatingFetcher:
    """curl_cffi-backed fetcher with impersonation + proxy rotation.

    Can be used directly, or passed to :class:`~supescrapper.scraper.Scraper`
    via ``fetch(..., impersonate="chrome124")``.
    """

    def __init__(
        self,
        *,
        impersonate: str = DEFAULT_IMPERSONATE,
        proxies: Sequence[str] | None = None,
        proxy_strategy: str = "round_robin",
        timeout: int = 30,
        retries: int = 3,
        backoff: float = 0.5,
        headers: dict[str, str] | None = None,
        http2: bool = True,
    ):
        self.impersonate = impersonate
        self.proxies = ProxyPool(proxies, strategy=proxy_strategy)
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.headers = dict(headers or {})
        self.http2 = http2

    def _build_session(self):
        curl = _require_curl_cffi()
        return curl.Session(impersonate=self.impersonate, headers=self.headers or None)

    def fetch(self, url: str, *, cookies: dict[str, str] | None = None,
              headers: dict[str, str] | None = None) -> str:
        """Fetch *url* and return the response body as text."""
        from .fetcher import _normalize_url
        url = _normalize_url(url)
        curl = _require_curl_cffi()
        session = curl.Session(impersonate=self.impersonate, headers=self.headers or None)

        last_exc: Exception | None = None
        for attempt in range(max(1, self.retries)):
            proxy = self.proxies.next()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                resp = session.get(
                    url,
                    timeout=self.timeout,
                    proxies=proxies,
                    cookies=cookies,
                    headers=headers,
                )
                status = getattr(resp, "status_code", 200)
                if status >= 400:
                    raise FetchError(f"HTTP {status} for {url}")
                return resp.text
            except Exception as exc:  # network/TLS/HTTP errors
                last_exc = exc
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (2 ** attempt))
        raise FetchError(f"Failed to fetch {url} after {self.retries} attempts: {last_exc}")


def status_is_retryable(exc: Exception) -> bool:
    """True if the error message indicates a retryable HTTP status."""
    msg = str(exc)
    return any(code in msg for code in ("429", "500", "502", "503", "504"))


class AsyncImpersonatingFetcher:
    """Async curl_cffi fetcher mirroring :class:`ImpersonatingFetcher`."""

    def __init__(
        self,
        *,
        impersonate: str = DEFAULT_IMPERSONATE,
        proxies: Sequence[str] | None = None,
        proxy_strategy: str = "round_robin",
        timeout: int = 30,
        retries: int = 3,
        backoff: float = 0.5,
        headers: dict[str, str] | None = None,
    ):
        self.impersonate = impersonate
        self.proxies = ProxyPool(proxies, strategy=proxy_strategy)
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.headers = dict(headers or {})

    async def fetch(self, url: str, *, cookies: dict[str, str] | None = None,
                    headers: dict[str, str] | None = None) -> str:
        import asyncio

        try:
            from curl_cffi.requests import AsyncSession  # type: ignore
        except ImportError as exc:
            raise ImpersonateNotInstalledError(
                "curl_cffi is required for impersonation/anti-bot fetching. "
                "Install it: pip install supescrapper[impersonate]"
            ) from exc

        from .fetcher import _normalize_url
        url = _normalize_url(url)

        last_exc: Exception | None = None
        for attempt in range(max(1, self.retries)):
            proxy = self.proxies.next()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                async with AsyncSession(impersonate=self.impersonate, headers=self.headers or None) as session:
                    resp = await session.get(
                        url, timeout=self.timeout, proxies=proxies,
                        cookies=cookies, headers=headers,
                    )
                    status = getattr(resp, "status_code", 200)
                    if status >= 400:
                        raise FetchError(f"HTTP {status} for {url}")
                    return resp.text
            except Exception as exc:
                last_exc = exc
            if attempt < self.retries - 1:
                await asyncio.sleep(self.backoff * (2 ** attempt))
        raise FetchError(f"Failed to fetch {url} after {self.retries} attempts: {last_exc}")
