from __future__ import annotations

import logging
from typing import Any

import requests

from .engine import ScrapeEngine
from .fetcher import _USER_AGENT, _build_session, fetch_dynamic, fetch_static
from .parser import is_spa

_log = logging.getLogger("supescrapper")


class Scraper(ScrapeEngine):
    """
    Main entry point — fluent API for web scraping.

    Usage:
        results = (
            Scraper()
            .fetch("https://example.com")
            .query(".product-card")
            .where_text("contains", "Sale")
            .extract()
        )
        results.export("json", "products.json")
    """

    def __init__(self) -> None:
        super().__init__()
        self._session: requests.Session | None = None
        self._session_key: tuple | None = None

    # --- session lifecycle -------------------------------------------------

    def _get_session(self, *, timeout: int, retries: int, user_agent: str | None,
                     proxies: dict[str, str] | None) -> requests.Session:
        key = (timeout, retries, user_agent, tuple(sorted((proxies or {}).items())))
        if self._session is None or self._session_key != key:
            self.close()
            self._session = _build_session(
                timeout=timeout, retries=retries,
                user_agent=user_agent or _USER_AGENT, proxies=proxies,
            )
            self._session_key = key
        return self._session

    def close(self) -> None:
        """Close the underlying HTTP session (safe to call repeatedly)."""
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None
                self._session_key = None

    def __enter__(self) -> Scraper:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- fetching ----------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        dynamic: bool | str = "auto",
        timeout: int = 30,
        retries: int = 3,
        user_agent: str | None = None,
        proxies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        wait_until: str = "domcontentloaded",
        viewport: dict[str, int] | None = None,
        scroll: int = 0,
        wait_for: str | None = None,
        wait_ms: int | None = None,
        impersonate: str | bool | None = None,
        proxy_pool: list[str] | None = None,
        proxy_strategy: str = "round_robin",
    ) -> Scraper:
        """
        Fetch a URL and parse its HTML.

        dynamic:
            True   — always use Playwright (JS rendering)
            False  — always use requests (static only)
            "auto" — try static first, fall back to Playwright if the page is a SPA
        impersonate:
            str    — use curl_cffi with this TLS/HTTP impersonation target
                     (e.g. "chrome124") — Level 3 anti-bot fetch
            True   — impersonate with the default target
        proxy_pool:
            List of proxy URLs to rotate through (used with impersonate).
        """
        use_impersonate = bool(impersonate)

        if use_impersonate:
            html = self._fetch_impersonate(
                url, impersonate=impersonate, timeout=timeout, retries=retries,
                user_agent=user_agent, headers=headers, cookies=cookies,
                proxies=proxies, proxy_pool=proxy_pool, proxy_strategy=proxy_strategy,
            )
            if is_spa(html) and dynamic is not False:
                _log.info("SPA detected — switching to Playwright for %s", url)
                html = self._fetch_dynamic(
                    url, timeout=timeout, user_agent=user_agent, viewport=viewport,
                    wait_until=wait_until, scroll=scroll, wait_for=wait_for, wait_ms=wait_ms,
                )
        elif dynamic is True:
            html = self._fetch_dynamic(
                url, timeout=timeout, user_agent=user_agent, viewport=viewport,
                wait_until=wait_until, scroll=scroll, wait_for=wait_for, wait_ms=wait_ms,
            )
        elif dynamic is False:
            html = self._fetch_static(
                url, timeout=timeout, retries=retries, user_agent=user_agent,
                proxies=proxies, headers=headers, cookies=cookies,
            )
        else:
            html = self._fetch_static(
                url, timeout=timeout, retries=retries, user_agent=user_agent,
                proxies=proxies, headers=headers, cookies=cookies,
            )
            if is_spa(html):
                _log.info("SPA detected — switching to Playwright for %s", url)
                html = self._fetch_dynamic(
                    url, timeout=timeout, user_agent=user_agent, viewport=viewport,
                    wait_until=wait_until, scroll=scroll, wait_for=wait_for, wait_ms=wait_ms,
                )

        self._load_html(html, url=url)
        return self

    def _fetch_impersonate(self, url: str, *, impersonate: str | bool, timeout: int,
                           retries: int, user_agent: str | None,
                           headers: dict[str, str] | None, cookies: dict[str, str] | None,
                           proxies: dict[str, str] | None, proxy_pool: list[str] | None,
                           proxy_strategy: str) -> str:
        from .impersonate import DEFAULT_IMPERSONATE, ImpersonatingFetcher

        target = DEFAULT_IMPERSONATE if impersonate is True else impersonate
        pool = list(proxy_pool or [])
        if not pool and proxies:
            # accept a single proxy dict as a degenerate pool
            single = proxies.get("https") or proxies.get("http")
            if single:
                pool = [single]

        merged_headers: dict[str, str] = {}
        if user_agent:
            merged_headers["User-Agent"] = user_agent
        if headers:
            merged_headers.update(headers)

        fetcher = ImpersonatingFetcher(
            impersonate=target, proxies=pool, proxy_strategy=proxy_strategy,
            timeout=timeout, retries=retries, headers=merged_headers,
        )
        return fetcher.fetch(url, cookies=cookies)

    def _fetch_static(self, url: str, *, timeout: int, retries: int,
                      user_agent: str | None, proxies: dict[str, str] | None,
                      headers: dict[str, str] | None, cookies: dict[str, str] | None) -> str:
        session = self._get_session(
            timeout=timeout, retries=retries, user_agent=user_agent, proxies=proxies,
        )
        return fetch_static(
            url, timeout=timeout, retries=retries, user_agent=user_agent or _USER_AGENT,
            proxies=proxies, headers=headers, cookies=cookies, session=session,
        )

    def _fetch_dynamic(self, url: str, *, timeout: int, user_agent: str | None,
                       viewport: dict[str, int] | None, wait_until: str,
                       scroll: int, wait_for: str | None, wait_ms: int | None) -> str:
        dyn_kwargs: dict[str, Any] = {
            "timeout": timeout * 1000,
            "wait_until": wait_until,
            "scroll": scroll,
            "wait_for": wait_for,
            "wait_ms": wait_ms,
        }
        if user_agent:
            dyn_kwargs["user_agent"] = user_agent
        if viewport:
            dyn_kwargs["viewport"] = viewport
        return fetch_dynamic(url, **dyn_kwargs)

    @staticmethod
    def scrape_feeds(*args: Any, **kwargs: Any):
        """Scrape multiple RSS / Atom feeds simultaneously."""
        from .feeds import scrape_feeds
        return scrape_feeds(*args, **kwargs)
