"""Tests for the Level 4 crawl engine (no network; injected fetch_fn)."""
from __future__ import annotations

import asyncio

import pytest

from supescrapper.crawler import (
    CrawlEngine,
    AsyncCrawlEngine,
    CrawlConfig,
    default_link_extractor,
    _normalize_crawl_url,
)
from supescrapper.models import ScrapedItem
from supescrapper.parser import parse

# A tiny in-memory site: url -> html
SITE = {
    "https://site.test/": '<a href="/a">A</a><a href="/b">B</a><a href="https://other.test/x">ext</a>',
    "https://site.test/a": '<a href="/c">C</a><a href="/">home</a>',
    "https://site.test/b": '<a href="/a">A again</a>',
    "https://site.test/c": "<p>leaf</p>",
    "https://other.test/x": "<p>external</p>",
}


def fake_fetch(url, config):
    if url not in SITE:
        raise RuntimeError(f"404 {url}")
    return SITE[url]


def builder(soup):
    return [ScrapedItem(tag="a", text=a.get_text(strip=True)) for a in soup.select("a")]


class TestNormalizeCrawlUrl:
    def test_strips_fragment(self):
        assert _normalize_crawl_url("https://x/y#frag") == "https://x/y"

    def test_strips_trailing_slash(self):
        assert _normalize_crawl_url("https://x/y/") == "https://x/y"


class TestDefaultLinkExtractor:
    def test_extracts_and_resolves(self):
        soup = parse('<a href="/a">A</a><a href="https://e.com/b">B</a>')
        links = default_link_extractor(soup, "https://site.test/")
        assert "https://site.test/a" in links
        assert "https://e.com/b" in links

    def test_skips_non_http_schemes(self):
        soup = parse('<a href="mailto:x@y.z">m</a><a href="#top">t</a><a href="/ok">o</a>')
        links = default_link_extractor(soup, "https://site.test/")
        assert links == ["https://site.test/ok"]


class TestCrawl:
    def test_crawls_whole_site(self):
        engine = CrawlEngine(CrawlConfig(max_urls=100), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        ok = {r.url for r in results if r.status == "ok"}
        assert ok == {
            "https://site.test/", "https://site.test/a",
            "https://site.test/b", "https://site.test/c",
        }

    def test_same_domain_only_default(self):
        engine = CrawlEngine(CrawlConfig(), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        assert all(r.url.startswith("https://site.test/") for r in results)

    def test_external_allowed_when_same_domain_off(self):
        engine = CrawlEngine(CrawlConfig(same_domain_only=False), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        assert any(r.url == "https://other.test/x" for r in results)

    def test_dedup_prevents_revisits(self):
        engine = CrawlEngine(CrawlConfig(max_urls=100), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        urls = [r.url for r in results]
        assert len(urls) == len(set(urls))

    def test_max_urls_limit(self):
        engine = CrawlEngine(CrawlConfig(max_urls=2), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        assert len(results) <= 2

    def test_max_depth(self):
        engine = CrawlEngine(CrawlConfig(max_depth=0), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        assert len(results) == 1
        assert results[0].depth == 0

    def test_deny_patterns(self):
        engine = CrawlEngine(CrawlConfig(deny_patterns=[r"/b$"]), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        assert all(not r.url.endswith("/b") for r in results)

    def test_allow_patterns(self):
        engine = CrawlEngine(CrawlConfig(allow_patterns=[r"/a"]), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/")
        # root allowed as seed; only /a matches thereafter
        assert {r.url for r in results} == {"https://site.test/", "https://site.test/a"}

    def test_follow_selector_pagination(self):
        pages = {
            "https://site.test/p1": '<a class="next" href="/p2">next</a>',
            "https://site.test/p2": '<a class="next" href="/p3">next</a>',
            "https://site.test/p3": "<p>end</p>",
        }

        def ff(url, cfg):
            return pages[url]

        engine = CrawlEngine(
            CrawlConfig(follow_selector="a.next", same_domain_only=True), fetch_fn=ff)
        results = engine.crawl("https://site.test/p1")
        urls = {r.url for r in results}
        assert urls == set(pages)

    def test_items_built_and_pipelines_applied(self):
        seen = []

        def pipe(item):
            seen.append(item.text)
            return item if item.text != "B" else None  # drop "B" items

        engine = CrawlEngine(CrawlConfig(max_depth=0), fetch_fn=fake_fetch, pipelines=[pipe])
        results = engine.crawl("https://site.test/", item_builder=builder)
        texts = [i.text for r in results for i in r.items]
        assert "A" in texts
        assert "B" not in texts

    def test_error_result_recorded(self):
        engine = CrawlEngine(CrawlConfig(retries=1), fetch_fn=fake_fetch)
        results = engine.crawl("https://site.test/missing")
        assert results[0].status == "error"

    def test_retry_middleware(self):
        calls = {"n": 0}

        def flaky(url, cfg):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "<p>ok</p>"

        engine = CrawlEngine(CrawlConfig(retries=3, max_depth=0), fetch_fn=flaky)
        results = engine.crawl("https://site.test/")
        assert results[0].status == "ok"
        assert calls["n"] == 2


class TestAsyncCrawl:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_async_crawls_site(self):
        async def ff(url, cfg):
            return SITE[url]

        engine = AsyncCrawlEngine(CrawlConfig(max_urls=100), fetch_fn=ff)
        results = self._run(engine.crawl("https://site.test/"))
        ok = {r.url for r in results if r.status == "ok"}
        assert "https://site.test/a" in ok
        assert len(ok) >= 3

    def test_async_dedup(self):
        async def ff(url, cfg):
            return SITE[url]

        engine = AsyncCrawlEngine(CrawlConfig(), fetch_fn=ff)
        results = self._run(engine.crawl("https://site.test/"))
        urls = [r.url for r in results]
        assert len(urls) == len(set(urls))

    def test_async_max_depth(self):
        async def ff(url, cfg):
            return SITE[url]

        engine = AsyncCrawlEngine(CrawlConfig(max_depth=0), fetch_fn=ff)
        results = self._run(engine.crawl("https://site.test/"))
        assert len(results) == 1

    def test_async_items(self):
        async def ff(url, cfg):
            return SITE[url]

        engine = AsyncCrawlEngine(CrawlConfig(max_depth=0), fetch_fn=ff)
        results = self._run(engine.crawl("https://site.test/", item_builder=builder))
        assert len(results[0].items) == 3
