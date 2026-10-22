"""Shared parsing/query engine used by both Scraper and AsyncScraper.

Only the *fetching* differs between the sync and async entry points; every
DOM operation lives here so the two APIs cannot drift apart.
"""
from __future__ import annotations

from typing import Callable

from bs4 import BeautifulSoup, Tag

from .classifier import ClassificationRule, ElementClassifier, get_classifier
from .exceptions import SelectorError
from .models import ScrapedItem, ScrapedResultSet
from .parser import (
    parse, select, select_xpath, select_by_regex, search_text,
    tags_to_items, extract_fields, extract_table, extract_links, extract_images,
)
from .query import QueryChain
from .schema import Schema, SchemaResultSet, get_schema


class ScrapeEngine:
    """Stateful DOM engine: parse HTML, query, filter, extract, classify."""

    def __init__(self) -> None:
        self._html: str = ""
        self._soup: BeautifulSoup | None = None
        self._results: ScrapedResultSet | None = None
        self._url: str = ""
        self._query_chain: QueryChain | None = None
        self._classifier: ElementClassifier = get_classifier()
        self._source_tags: list[Tag | None] = []
        self._schema_results: SchemaResultSet | None = None

    # --- loading -----------------------------------------------------------

    def _load_html(self, html: str, url: str = "") -> None:
        self._html = html
        self._soup = parse(html)
        self._url = url
        self._results = None
        self._query_chain = None
        self._source_tags = []
        self._schema_results = None

    def from_html(self, html: str):
        self._load_html(html, url="")
        return self

    def _require_soup(self, action: str) -> BeautifulSoup:
        if self._soup is None:
            raise SelectorError(f"Nothing to {action} — call fetch() or from_html() first.")
        return self._soup

    def _set_results(self, tags: list[Tag]) -> ScrapedResultSet:
        items = tags_to_items(tags, base_url=self._url)
        self._source_tags = list(tags)
        rs = ScrapedResultSet(items)
        rs.source_url = self._url
        self._results = rs
        self._query_chain = QueryChain(rs)
        return rs

    # --- querying ----------------------------------------------------------

    def query(self, selector: str, *, mode: str = "css"):
        soup = self._require_soup("query")
        dispatch = {
            "css": lambda: select(soup, selector),
            "xpath": lambda: select_xpath(soup, selector),
            "regex": lambda: select_by_regex(soup, selector),
        }
        fn = dispatch.get(mode)
        if fn is None:
            raise ValueError(f"Unknown query mode '{mode}'. Choose from: {list(dispatch)}")
        self._set_results(fn())
        return self

    def search(self, term: str, *, case_sensitive: bool = False):
        soup = self._require_soup("search")
        items = search_text(soup, term, case_sensitive=case_sensitive)
        self._source_tags = [None] * len(items)
        rs = ScrapedResultSet(items)
        rs.source_url = self._url
        self._results = rs
        self._query_chain = QueryChain(rs)
        return self

    # --- filtering ---------------------------------------------------------

    def _chain(self) -> QueryChain:
        if self._query_chain is None:
            raise SelectorError("No active query — call .query() first.")
        return self._query_chain

    def where_text(self, op: str, value: str):
        self._chain().where_text(op, value)
        return self

    def where_attr(self, attr: str, op: str, value: str):
        self._chain().where_attr(attr, op, value)
        return self

    def where_tag(self, tag_name: str):
        self._chain().where_tag(tag_name)
        return self

    def where_url(self, op: str, value: str):
        self._chain().where_url(op, value)
        return self

    def where(self, predicate: Callable[[ScrapedItem], bool]):
        self._chain().where(predicate)
        return self

    def limit(self, n: int):
        # Preserve the tag<->item alignment while eagerly materializing.
        self._finalize()
        pairs = list(zip(self._results, self._source_tags))[:n]
        items = [p[0] for p in pairs]
        self._source_tags = [p[1] for p in pairs]
        rs = ScrapedResultSet(items)
        rs.source_url = self._url
        self._results = rs
        self._query_chain = QueryChain(rs)
        return self

    def _finalize(self) -> None:
        if self._query_chain is None or self._results is None:
            return
        chain = self._query_chain
        kept_items: list[ScrapedItem] = []
        kept_tags: list[Tag | None] = []
        for item, tag in zip(self._results, self._source_tags):
            if chain.matches(item):
                kept_items.append(item)
                kept_tags.append(tag)
        rs = ScrapedResultSet(kept_items)
        rs.source_url = self._results.source_url
        self._results = rs
        self._source_tags = kept_tags
        self._query_chain = None

    # --- extraction --------------------------------------------------------

    def _reparse(self, html: str) -> Tag | None:
        inner = BeautifulSoup(html, "lxml")
        return inner.find()

    def extract(self, **field_selectors: str) -> ScrapedResultSet:
        self._finalize()
        if not field_selectors:
            return self._results

        items: list[ScrapedItem] = []
        for src_item, src_tag in zip(self._results, self._source_tags):
            tag = src_tag if isinstance(src_tag, Tag) else self._reparse(src_item.html)
            if tag is None:
                continue
            fields = extract_fields(tag, **field_selectors)
            item_text = fields.pop("text", src_item.text) if "text" in field_selectors else src_item.text
            items.append(ScrapedItem(
                tag=src_item.tag,
                text=item_text,
                html=src_item.html,
                attrs=dict(src_item.attrs),
                url=src_item.url,
                meta=fields,
            ))
        out = ScrapedResultSet(items)
        out.source_url = self._results.source_url
        self._results = out
        self._source_tags = [None] * len(items)
        return out

    def extract_table(self, table_selector: str = "table") -> list[dict[str, str]]:
        soup = self._require_soup("parse")
        return extract_table(soup, table_selector)

    def extract_links(self, selector: str = "a[href]") -> ScrapedResultSet:
        soup = self._require_soup("parse")
        items = extract_links(soup, selector)
        out = ScrapedResultSet(items)
        out.source_url = self._url
        return out

    def extract_images(self, selector: str = "img[src]") -> ScrapedResultSet:
        soup = self._require_soup("parse")
        items = extract_images(soup, selector)
        out = ScrapedResultSet(items)
        out.source_url = self._url
        return out

    # --- classification ----------------------------------------------------

    def classify(self):
        self._finalize()
        soup = self._require_soup("classify")
        classified: list[ScrapedItem] = []
        for item, tag in zip(self._results, self._source_tags):
            if not isinstance(tag, Tag):
                tag = self._reparse(item.html)
            types = self._classifier.classify(tag, soup) if isinstance(tag, Tag) else []
            classified.append(ScrapedItem(
                tag=item.tag, text=item.text, html=item.html,
                attrs=dict(item.attrs), url=item.url, meta=dict(item.meta),
                classification=types,
            ))
        out = ScrapedResultSet(classified)
        out.source_url = self._results.source_url
        self._results = out
        self._source_tags = [None] * len(classified)
        self._query_chain = QueryChain(out)
        return self

    def _tagged_results(self, tags: list[Tag]) -> ScrapedResultSet:
        items = tags_to_items(tags, base_url=self._url)
        for item, tag in zip(items, tags):
            item.classification = self._classifier.classify(tag, self._soup)
        rs = ScrapedResultSet(items)
        rs.source_url = self._url
        self._results = rs
        self._source_tags = list(tags)
        self._query_chain = QueryChain(rs)
        return rs

    def find_elements(self, element_type: str) -> ScrapedResultSet:
        soup = self._require_soup("find")
        return self._tagged_results(self._classifier.find(element_type, soup))

    def find_interactive(self) -> ScrapedResultSet:
        soup = self._require_soup("find")
        return self._tagged_results(self._classifier.find_interactive(soup))

    def register_classifier(self, rule: ClassificationRule):
        self._classifier.register(rule)
        return self

    # --- schema extraction ---------------------------------------------------

    def scrape(self, schema: "str | Schema", *, strict: bool | None = None) -> SchemaResultSet:
        """Extract structured records using a named (or inline) :class:`Schema`.

        Runs against the loaded document; if a ``.query()`` chain is active,
        the matched elements become the record roots (so
        ``.query(".post").where_text(...).scrape("news")`` works). Otherwise
        the schema's own ``item`` selector decides the roots.

        Returns a :class:`SchemaResultSet` — a list of plain dicts with
        ``.to_json()`` / ``.export()`` helpers.
        """
        soup = self._require_soup("scrape")
        self._finalize()
        sch = get_schema(schema)
        roots = None
        if self._source_tags:
            tags = [t for t in self._source_tags if isinstance(t, Tag)]
            if tags:
                roots = tags
        records = sch.extract(soup, base_url=self._url, roots=roots, strict=strict)
        rs = SchemaResultSet(records, schema=sch.name, source_url=self._url)
        self._schema_results = rs
        return rs

    # --- accessors ---------------------------------------------------------

    @property
    def html(self) -> str:
        return self._html

    @property
    def results(self) -> ScrapedResultSet | None:
        self._finalize()
        return self._results
