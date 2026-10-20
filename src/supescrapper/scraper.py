from __future__ import annotations

from typing import Any

from .classifier import ClassificationRule, ElementClassifier, get_classifier
from .exceptions import SelectorError
from .fetcher import fetch_static, fetch_dynamic
from .parser import (
    parse, is_spa, select, select_xpath, select_by_regex, search_text,
    tags_to_items, extract_fields, extract_table, extract_links, extract_images,
)
from .models import ScrapedItem, ScrapedResultSet
from .query import QueryChain


class Scraper:
    """
    Main entry point — fluent API for web scraping.

    Usage:
        results = (
            Scraper()
            .fetch("https://example.com")
            .query(".product-card")
            .where_text("contains", "Sale")
            .build()
        )
        results.export("json", "products.json")
    """

    def __init__(self):
        self._html: str = ""
        self._soup = None
        self._results: ScrapedResultSet | None = None
        self._url: str = ""
        self._query_chain: QueryChain | None = None
        self._classifier: ElementClassifier = get_classifier()

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
    ) -> "Scraper":
        """
        Fetch a URL and parse its HTML.

        dynamic:
            True   — always use Playwright (JS rendering)
            False  — always use requests (static only)
            "auto" — try static first, fall back to Playwright if the page is a SPA
        """
        self._url = url
        kwargs: dict[str, Any] = {}
        if user_agent:
            kwargs["user_agent"] = user_agent
        if proxies:
            kwargs["proxies"] = proxies

        if dynamic is True:
            self._html = self._fetch_dynamic(url, timeout=timeout, user_agent=user_agent, viewport=viewport, wait_until=wait_until, **kwargs)
        elif dynamic is False:
            self._html = self._fetch_static(url, timeout=timeout, retries=retries, headers=headers, cookies=cookies, **kwargs)
        else:
            # auto mode: try static, detect SPA, fallback to dynamic
            self._html = self._fetch_static(url, timeout=timeout, retries=retries, headers=headers, cookies=cookies, **kwargs)
            if is_spa(self._html):
                import sys
                print("[supescrapper] SPA detected — switching to Playwright...", file=sys.stdout)
                self._html = self._fetch_dynamic(url, timeout=timeout, user_agent=user_agent, viewport=viewport, wait_until=wait_until, **kwargs)

        self._soup = parse(self._html)
        self._results = None
        self._query_chain = None
        return self

    def _fetch_static(self, url: str, **kwargs: Any) -> str:
        return fetch_static(url, **kwargs)

    def _fetch_dynamic(self, url: str, *, timeout: int = 30, user_agent: str | None = None, viewport: dict[str, int] | None = None, wait_until: str = "domcontentloaded", **_extra: Any) -> str:
        dyn_kwargs: dict[str, Any] = {"timeout": timeout * 1000, "wait_until": wait_until}
        if user_agent:
            dyn_kwargs["user_agent"] = user_agent
        if viewport:
            dyn_kwargs["viewport"] = viewport
        return fetch_dynamic(url, **dyn_kwargs)

    def search(self, term: str, *, case_sensitive: bool = False) -> "Scraper":
        """Full-text search — find all elements containing *term* anywhere on the page."""
        if self._soup is None:
            raise SelectorError("Nothing to search — call fetch() or from_html() first.")
        items = search_text(self._soup, term, case_sensitive=case_sensitive)
        self._results = ScrapedResultSet(items)
        self._results.source_url = self._url
        self._query_chain = QueryChain(self._results)
        return self

    def from_html(self, html: str) -> "Scraper":
        self._html = html
        self._soup = parse(html)
        self._results = None
        self._query_chain = None
        self._url = ""
        return self

    def query(self, selector: str, *, mode: str = "css") -> "Scraper":
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

    def where_text(self, op: str, value: str) -> "Scraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_text(op, value)
        return self

    def where_attr(self, attr: str, op: str, value: str) -> "Scraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_attr(attr, op, value)
        return self

    def where_tag(self, tag_name: str) -> "Scraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_tag(tag_name)
        return self

    def where_url(self, op: str, value: str) -> "Scraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where_url(op, value)
        return self

    def where(self, predicate) -> "Scraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._query_chain.where(predicate)
        return self

    def limit(self, n: int) -> "Scraper":
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        self._results = self._query_chain.limit(n)
        self._query_chain = QueryChain(self._results)
        return self

    def extract(self, **field_selectors: str) -> ScrapedResultSet:
        """
        If field_selectors given: re-extract structured fields from matched elements.
        Otherwise: return current filtered results as-is.

        Usage:
            .extract(title="h2.text", price="span.price.text", link="a@href")
        """
        self._finalize()
        if not field_selectors:
            return self._results

        items: list[ScrapedItem] = []
        for src_item in self._results:
            from bs4 import BeautifulSoup
            inner = BeautifulSoup(src_item.html, "lxml")
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

    def classify(self) -> "Scraper":
        """Tag all current results with their detected element types."""
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
        """Find all elements matching a specific type (e.g., 'button', 'search_input')."""
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
        return self

    def find_interactive(self) -> ScrapedResultSet:
        """Find all interactive elements (buttons, inputs, links, selects, etc.)."""
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
        return self

    def register_classifier(self, rule: ClassificationRule) -> "Scraper":
        """Add a custom classification rule."""
        self._classifier.register(rule)
        return self

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
