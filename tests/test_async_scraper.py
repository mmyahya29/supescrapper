"""Tests for AsyncScraper (mocked network) and sync/async parity."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supescrapper import AsyncScraper, Scraper
from supescrapper.exceptions import FetchError, SelectorError

PAGE = """
<html><body>
  <nav aria-label="main nav"><a href="/a">A</a><a href="/b">B</a></nav>
  <button>OK</button>
  <input type="search" placeholder="Search">
  <div class="card"><h2>Widget</h2><span class="price">$9.99</span>
    <a href="/widget">More</a></div>
</body></html>
"""


def _run(coro):
    return asyncio.run(coro)


class TestAsyncFetch:
    def test_static_fetch_success(self):
        mock_resp = MagicMock()
        mock_resp.text = PAGE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            async def run():
                scraper = AsyncScraper()
                await scraper.fetch("https://example.com", dynamic=False)
                return scraper.query("button").extract()
            result = _run(run())
        assert len(result) == 1

    def test_static_fetch_failure_raises(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(FetchError, match="Failed to fetch"):
                _run(AsyncScraper().fetch("https://bad.example", dynamic=False, retries=2))
        mock_client.get.assert_awaited()


class TestAsyncParity:
    """AsyncScraper must behave identically to Scraper for a given DOM."""

    def _both(self, fn_sync, fn_async):
        sync = Scraper().from_html(PAGE)
        async_ = AsyncScraper().from_html(PAGE)
        return fn_sync(sync), fn_async(async_)

    def test_query_extract_parity(self):
        s = Scraper().from_html(PAGE).query("a").extract()
        a = AsyncScraper().from_html(PAGE).query("a").extract()
        assert [i.text for i in s] == [i.text for i in a]
        assert [i.url for i in s] == [i.url for i in a]

    def test_named_field_extract_parity(self):
        s = Scraper().from_html(PAGE).query(".card").extract(name="h2.text", price="span.price")
        a = AsyncScraper().from_html(PAGE).query(".card").extract(name="h2.text", price="span.price")
        assert s[0]["name"] == a[0]["name"] == "Widget"
        assert s[0]["price"] == a[0]["price"] == "$9.99"

    def test_find_elements_returns_resultset(self):
        r = AsyncScraper().from_html(PAGE).find_elements("button")
        assert len(r) == 1
        assert r[0].text == "OK"

    def test_search_parity(self):
        s = Scraper().from_html(PAGE).search("Widget").extract()
        a = AsyncScraper().from_html(PAGE).search("Widget").extract()
        assert [i.text for i in s] == [i.text for i in a]

    def test_filter_parity(self):
        s = Scraper().from_html(PAGE).query("a").where_text("contains", "A").extract()
        a = AsyncScraper().from_html(PAGE).query("a").where_text("contains", "A").extract()
        assert [i.text for i in s] == [i.text for i in a]

    def test_xpath_parity(self):
        s = Scraper().from_html(PAGE).query("//button", mode="xpath").extract()
        a = AsyncScraper().from_html(PAGE).query("//button", mode="xpath").extract()
        assert [i.text for i in s] == [i.text for i in a]


class TestAsyncErrors:
    def test_query_before_load_raises(self):
        with pytest.raises(SelectorError):
            AsyncScraper().query("h1")

    def test_find_before_load_raises(self):
        with pytest.raises(SelectorError):
            AsyncScraper().find_interactive()
