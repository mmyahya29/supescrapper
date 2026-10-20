"""
AsyncScraper — async version of the Scraper using httpx + Playwright async.

Requires optional dependencies:
    pip install supescrapper[async]
    pip install supescrapper[async-dynamic]
"""
from __future__ import annotations

from typing import Any

from .classifier import ClassificationRule, ElementClassifier, get_classifier
from .exceptions import SelectorError, FetchError, DynamicPageError
from .parser import (
    parse, is_spa, select, select_xpath, select_by_regex, search_text,
    tags_to_items, extract_fields, extract_table, extract_links, extract_images,
)
from .models import ScrapedItem, ScrapedResultSet
from .query import QueryChain


class AsyncScraper:
    """
    Async entry point — fluent API for web scraping with async/await.

    Usage:
        async def main():
            results = await AsyncScraper().fetch("https://example.com").query("h1").extract()
    """

    def __init__(self) -> None:
        self._html: str = ""
        self._soup = None
        self._results: ScrapedResultSet | None = None
        self._url: str = ""
        self._query_chain: QueryChain | None = None
        self._classifier: ElementClassifier = get_classifier()

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
        wait_until: str = "networkidle",
        viewport: dict[str, int] | None = None,
    ) -> "AsyncScraper":
        """Fetch a URL asynchronously. Auto-detects SPAs."""
        self._url = url

        if dynamic is True:
            self._html = await self._fetch_dynamic(url, timeout=timeout, user_agent=user_agent, viewport=viewport, wait_until=wait_until)
        elif dynamic is False:
            self._html = await self._fetch_static(url, timeout=timeout, retries=retries, headers=headers, cookies=cookies, proxies=proxies, user_agent=user_agent)
        else:
            self._html = await self._fetch_static(url, timeout=timeout, retries=retries, headers=headers, cookies=cookies, proxies=proxies, user_agent=user_agent)
            if is_spa(self._html):
                import sys
                print("[supescrapper] SPA detected — switching to Playwright...", file=sys.stdout)
                self._html = await self._fetch_dynamic(url, timeout=timeout, user_agent=user_agent, viewport=viewport, wait_until=wait_until)

        self._soup = parse(self._html)
        self._results = None
        self._query_chain = None
        return self

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

        from .fetcher import _USER_AGENT
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
        wait_until: str = "networkidle",
    ) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise DynamicPageError(
                "Playwright is required for dynamic pages. "
                "Install: pip install supescrapper[dynamic] && playwright install"
            )

        from .fetcher import _USER_AGENT
        ua = user_agent or _USER_AGENT
        vp = viewport or {"width": 1920, "height": 1080}

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=ua, viewport=vp)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
            except Exception as exc:
                await browser.close()
                raise FetchError(f"Failed to load {url} with Playwright: {exc}") from exc
            html = await page.content()
            await browser.close()
        return html

    def from_html(self, html: str) -> "AsyncScraper":
        self._html = html
        self._soup = parse(html)
        self._results = None
        self._query_chain = None
        self._url = ""
        return self

    def query(self, selector: str, *, mode: str = "css") -> "AsyncScraper":
        if self._soup is None:
            raise SelectorError("Nothing to query — call fetch() or from_html() first.")
        dispatch = {
            "css": lambda: select(self._soup, selector),
            "xpath": lambda: select_xpath(self._soup, selector),
            "regex": lambda: select_by_regex(self._soup, selector),
        }
        fn = dispatch.get(mode)
        if fn is None:
            raise ValueError(f"Unknown query mode '{mode}'. Choose from: {list(dispatch)}")
        tags = fn()
        self._results = ScrapedResultSet(tags_to_items(tags, base_url=self._url))
        self._results.source_url = self._url
        self._query_chain = QueryChain(self._results)
        return self

    def search(self, term: str, *, case_sensitive: bool = False) -> "AsyncScraper":
        if self._soup is None:
            raise SelectorError("Nothing to search — call fetch() or from_html() first.")
        items = search_text(self._soup, term, case_sensitive=case_sensitive)
        self._results = ScrapedResultSet(items)
        self._results.source_url = self._url
        self._query_chain = QueryChain(self._results)
        return self

    def where_text(self, op: str, value: str) -> "AsyncScraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_text(op, value)
        return self

    def where_attr(self, attr: str, op: str, value: str) -> "AsyncScraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_attr(attr, op, value)
        return self

    def where_tag(self, tag_name: str) -> "AsyncScraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_tag(tag_name)
        return self

    def where_url(self, op: str, value: str) -> "AsyncScraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_url(op, value)
        return self

    def where(self, predicate) -> "AsyncScraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where(predicate)
        return self

    def limit(self, n: int) -> "AsyncScraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._results = self._query_chain.limit(n)
        self._query_chain = QueryChain(self._results)
        return self

    def extract(self, **field_selectors: str) -> ScrapedResultSet:
        self._finalize()
        if not field_selectors:
            return self._results

        items: list[ScrapedItem] = []
        for src_item in self._results:
            inner = parse(src_item.html)
            tag = inner.find()
            if tag is None:
                continue
            fields = extract_fields(tag, **field_selectors)
            merged_attrs = dict(src_item.attrs)
            item_text = fields.pop("text", src_item.text) if "text" in field_selectors else src_item.text
            items.append(ScrapedItem(
                tag=src_item.tag,
                text=item_text,
                html=src_item.html,
                attrs=merged_attrs,
                url=src_item.url,
                meta=fields,
            ))
        out = ScrapedResultSet(items)
        out.source_url = self._results.source_url
        self._results = out
        return self._results

    def classify(self) -> "AsyncScraper":
        self._finalize()
        if self._soup is None or self._results is None:
            raise SelectorError("Nothing to classify — call fetch() and query() first.")
        soup = self._soup
        classified_items: list[ScrapedItem] = []
        for item in self._results:
            inner = parse(item.html)
            tag = inner.find()
            if tag is None:
                classified_items.append(item)
                continue
            types = self._classifier.classify(tag, soup)
            new_item = ScrapedItem(
                tag=item.tag,
                text=item.text,
                html=item.html,
                attrs=dict(item.attrs),
                url=item.url,
                meta=dict(item.meta),
                classification=types,
            )
            classified_items.append(new_item)
        out = ScrapedResultSet(classified_items)
        out.source_url = self._results.source_url
        self._results = out
        self._query_chain = QueryChain(self._results)
        return self

    def find_elements(self, element_type: str) -> ScrapedResultSet:
        if self._soup is None:
            raise SelectorError("Nothing to find — call fetch() or from_html() first.")
        tags = self._classifier.find(element_type, self._soup)
        items = tags_to_items(tags, base_url=self._url)
        for item in items:
            inner = parse(item.html)
            tag = inner.find()
            if tag is not None:
                item.classification = self._classifier.classify(tag, self._soup)
        out = ScrapedResultSet(items)
        out.source_url = self._url
        self._results = out
        self._query_chain = QueryChain(self._results)
        return out

    def find_interactive(self) -> ScrapedResultSet:
        if self._soup is None:
            raise SelectorError("Nothing to find — call fetch() or from_html() first.")
        tags = self._classifier.find_interactive(self._soup)
        items = tags_to_items(tags, base_url=self._url)
        for item in items:
            inner = parse(item.html)
            tag = inner.find()
            if tag is not None:
                item.classification = self._classifier.classify(tag, self._soup)
        out = ScrapedResultSet(items)
        out.source_url = self._url
        self._results = out
        self._query_chain = QueryChain(self._results)
        return out

    def extract_table(self, table_selector: str = "table") -> list[dict[str, str]]:
        self._finalize()
        if self._soup is None:
            raise SelectorError("Nothing parsed — call fetch() first.")
        return extract_table(self._soup, table_selector)

    def extract_links(self, selector: str = "a[href]") -> ScrapedResultSet:
        self._finalize()
        if self._soup is None:
            raise SelectorError("Nothing parsed — call fetch() first.")
        items = extract_links(self._soup, selector)
        out = ScrapedResultSet(items)
        out.source_url = self._url
        return out

    def extract_images(self, selector: str = "img[src]") -> ScrapedResultSet:
        self._finalize()
        if self._soup is None:
            raise SelectorError("Nothing parsed — call fetch() first.")
        items = extract_images(self._soup, selector)
        out = ScrapedResultSet(items)
        out.source_url = self._url
        return out

    def _finalize(self) -> None:
        if self._query_chain is not None and self._results is not None:
            self._results = self._query_chain.build()
            self._query_chain = None

    @property
    def html(self) -> str:
        return self._html

    @property
    def results(self) -> ScrapedResultSet | None:
        self._finalize()
        return self._results
