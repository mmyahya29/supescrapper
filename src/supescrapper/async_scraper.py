"""
AsyncScraper — async version of the Scraper using httpx + Playwright async.

Requires optional dependencies:
    pip install supescrapper[async]
    pip install supescrapper[async-dynamic]
"""
from __future__ import annotations

import logging
from typing import Any

from .engine import ScrapeEngine
from .exceptions import DynamicPageError, FetchError
from .parser import is_spa

_log = logging.getLogger("supescrapper")


class AsyncScraper(ScrapeEngine):
    """
    Async entry point — fluent API for web scraping with async/await.

    Usage:
        async def main():
            async with AsyncScraper() as scraper:
                await scraper.fetch("https://example.com")
                results = scraper.query("h1").extract()
    """

    async def __aenter__(self) -> "AsyncScraper":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def fetch(
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
    ) -> "AsyncScraper":
        """Fetch a URL asynchronously. Auto-detects SPAs. Supports impersonation."""
        use_impersonate = bool(impersonate)

        if use_impersonate:
            html = await self._fetch_impersonate(
                url, impersonate=impersonate, timeout=timeout, retries=retries,
                headers=headers, cookies=cookies, proxies=proxies,
                proxy_pool=proxy_pool, proxy_strategy=proxy_strategy,
            )
            if is_spa(html) and dynamic is not False:
                _log.info("SPA detected — switching to Playwright for %s", url)
                html = await self._fetch_dynamic(
                    url, timeout=timeout, user_agent=user_agent, viewport=viewport,
                    wait_until=wait_until, scroll=scroll, wait_for=wait_for, wait_ms=wait_ms,
                )
        elif dynamic is True:
            html = await self._fetch_dynamic(
                url, timeout=timeout, user_agent=user_agent, viewport=viewport,
                wait_until=wait_until, scroll=scroll, wait_for=wait_for, wait_ms=wait_ms,
            )
        elif dynamic is False:
            html = await self._fetch_static(
                url, timeout=timeout, retries=retries, headers=headers,
                cookies=cookies, proxies=proxies, user_agent=user_agent,
            )
        else:
            html = await self._fetch_static(
                url, timeout=timeout, retries=retries, headers=headers,
                cookies=cookies, proxies=proxies, user_agent=user_agent,
            )
            if is_spa(html):
                _log.info("SPA detected — switching to Playwright for %s", url)
                html = await self._fetch_dynamic(
                    url, timeout=timeout, user_agent=user_agent, viewport=viewport,
                    wait_until=wait_until, scroll=scroll, wait_for=wait_for, wait_ms=wait_ms,
                )

        self._load_html(html, url=url)
        return self

    async def _fetch_impersonate(self, url: str, *, impersonate: str | bool, timeout: int,
                                 retries: int, headers: dict[str, str] | None,
                                 cookies: dict[str, str] | None,
                                 proxies: dict[str, str] | None, proxy_pool: list[str] | None,
                                 proxy_strategy: str) -> str:
        from .impersonate import AsyncImpersonatingFetcher, DEFAULT_IMPERSONATE

        target = DEFAULT_IMPERSONATE if impersonate is True else impersonate
        pool = list(proxy_pool or [])
        if not pool and proxies:
            single = proxies.get("https") or proxies.get("http")
            if single:
                pool = [single]

        fetcher = AsyncImpersonatingFetcher(
            impersonate=target, proxies=pool, proxy_strategy=proxy_strategy,
            timeout=timeout, retries=retries, headers=headers,
        )
        return await fetcher.fetch(url, cookies=cookies)

    async def _fetch_static(
        self,
        url: str,
        *,
        timeout: int = 30,
        retries: int = 3,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        proxies: dict[str, str] | None = None,
        user_agent: str | None = None,
    ) -> str:
        try:
            import httpx
        except ImportError:
            raise FetchError("httpx is required for AsyncScraper. Install: pip install httpx")

        from .fetcher import _normalize_url, _USER_AGENT
        url = _normalize_url(url)
        ua = user_agent or _USER_AGENT
        default_headers = {"User-Agent": ua}
        if headers:
            default_headers.update(headers)

        proxy_url = None
        if proxies:
            proxy_url = proxies.get("https") or proxies.get("http")

        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    proxy=proxy_url,
                    timeout=timeout,
                ) as client:
                    resp = await client.get(url, headers=default_headers, cookies=cookies)
                    resp.raise_for_status()
                    return resp.text
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    import asyncio
                    await asyncio.sleep(0.5 * (2 ** attempt))
        raise FetchError(f"Failed to fetch {url} after {retries} attempts: {last_exc}")

    async def _fetch_dynamic(
        self,
        url: str,
        *,
        timeout: int = 30,
        user_agent: str | None = None,
        viewport: dict[str, int] | None = None,
        wait_until: str = "domcontentloaded",
        scroll: int = 0,
        wait_for: str | None = None,
        wait_ms: int | None = None,
    ) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise DynamicPageError(
                "Playwright is required for dynamic pages. "
                "Install: pip install supescrapper[dynamic] && playwright install"
            )

        from .fetcher import _normalize_url, _USER_AGENT
        url = _normalize_url(url)
        ua = user_agent or _USER_AGENT
        vp = viewport or {"width": 1920, "height": 1080}

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(user_agent=ua, viewport=vp)
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
                    if wait_for:
                        await page.wait_for_selector(wait_for, timeout=timeout * 1000)
                    if wait_ms:
                        await page.wait_for_timeout(wait_ms)
                    for _ in range(max(0, scroll)):
                        await page.mouse.wheel(0, 1000)
                        await page.wait_for_timeout(1000)
                except Exception as exc:
                    raise FetchError(f"Failed to load {url} with Playwright: {exc}") from exc
                return await page.content()
            finally:
                await browser.close()

    @staticmethod
    async def scrape_feeds(*args: Any, **kwargs: Any):
        """Asynchronously scrape multiple RSS / Atom feeds simultaneously."""
        from .feeds import async_scrape_feeds
        return await async_scrape_feeds(*args, **kwargs)
