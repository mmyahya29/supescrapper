"""Tests for fetcher module (mocked HTTP)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from supescrapper.fetcher import fetch_static, fetch_dynamic, _normalize_url
from supescrapper.exceptions import FetchError, DynamicPageError


class TestNormalizeUrl:
    def test_adds_https_when_missing(self):
        assert _normalize_url("example.com") == "https://example.com"

    def test_preserves_existing_scheme(self):
        assert _normalize_url("http://example.com") == "http://example.com"
        assert _normalize_url("https://example.com") == "https://example.com"

    def test_host_with_port(self):
        assert _normalize_url("example.com:8080/path") == "https://example.com:8080/path"

    def test_protocol_relative(self):
        assert _normalize_url("//example.com/x") == "https://example.com/x"

    def test_empty(self):
        assert _normalize_url("   ") == ""


class TestFetchStatic:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Hello</body></html>"
        mock_resp.raise_for_status = MagicMock()

        with patch("supescrapper.fetcher.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            html = fetch_static("https://example.com")
            assert html == "<html><body>Hello</body></html>"

    def test_reused_session_not_closed(self):
        mock_resp = MagicMock()
        mock_resp.text = "ok"
        mock_resp.raise_for_status = MagicMock()
        session = MagicMock()
        session.get.return_value = mock_resp

        html = fetch_static("https://example.com", session=session)
        assert html == "ok"
        session.close.assert_not_called()

    def test_owned_session_closed(self):
        mock_resp = MagicMock()
        mock_resp.text = "ok"
        mock_resp.raise_for_status = MagicMock()

        with patch("supescrapper.fetcher.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session
            fetch_static("https://example.com")
            mock_session.close.assert_called_once()

    def test_failure_raises_fetch_error(self):
        import requests
        with patch("supescrapper.fetcher.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.side_effect = requests.RequestException("Connection refused")
            mock_session_cls.return_value = mock_session

            with pytest.raises(FetchError, match="Failed to fetch"):
                fetch_static("https://bad-url.example")

    def test_custom_headers(self):
        mock_resp = MagicMock()
        mock_resp.text = "ok"
        mock_resp.raise_for_status = MagicMock()

        with patch("supescrapper.fetcher.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            fetch_static("https://example.com", headers={"X-Custom": "value"})
            mock_session.headers.update.assert_any_call({"X-Custom": "value"})

    def test_custom_user_agent(self):
        mock_resp = MagicMock()
        mock_resp.text = "ok"
        mock_resp.raise_for_status = MagicMock()

        with patch("supescrapper.fetcher.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            fetch_static("https://example.com", user_agent="MyBot/1.0")
            mock_session.headers.update.assert_any_call({"User-Agent": "MyBot/1.0"})


class TestFetchDynamic:
    def _fake_playwright(self, html="<html><body>rendered</body></html>"):
        page = MagicMock()
        page.content.return_value = html
        ctx = MagicMock()
        ctx.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = ctx

        pw = MagicMock()
        pw.chromium.launch.return_value = browser

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=pw)
        cm.__exit__ = MagicMock(return_value=False)
        return cm, browser, page

    def test_returns_rendered_html(self):
        cm, browser, page = self._fake_playwright()
        with patch("playwright.sync_api.sync_playwright", return_value=cm):
            html = fetch_dynamic("https://example.com")
        assert html == "<html><body>rendered</body></html>"
        browser.close.assert_called_once()

    def test_scroll_triggers_wheel(self):
        cm, browser, page = self._fake_playwright()
        with patch("playwright.sync_api.sync_playwright", return_value=cm):
            fetch_dynamic("https://example.com", scroll=3, scroll_delay_ms=1)
        assert page.mouse.wheel.call_count == 3

    def test_wait_for_selector(self):
        cm, browser, page = self._fake_playwright()
        with patch("playwright.sync_api.sync_playwright", return_value=cm):
            fetch_dynamic("https://example.com", wait_for=".card")
        page.wait_for_selector.assert_called_once()

    def test_browser_closed_on_navigation_error(self):
        cm, browser, page = self._fake_playwright()
        page.goto.side_effect = RuntimeError("nav failed")
        with patch("playwright.sync_api.sync_playwright", return_value=cm):
            with pytest.raises(FetchError):
                fetch_dynamic("https://example.com")
        browser.close.assert_called_once()

    def test_missing_playwright_raises(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("no playwright")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(DynamicPageError):
                fetch_dynamic("https://example.com")


class TestScraperFetchKwargs:
    def test_user_agent_with_dynamic_true(self):
        from supescrapper import Scraper
        with patch("supescrapper.scraper.fetch_dynamic", return_value="<html><body><h1>x</h1></body></html>") as fd:
            Scraper().fetch("https://example.com", dynamic=True, user_agent="MyBot/1.0")
        assert fd.call_args.kwargs["user_agent"] == "MyBot/1.0"

    def test_session_reused_across_fetches(self):
        from supescrapper import Scraper
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><h1>x</h1></body></html>"
        mock_resp.raise_for_status = MagicMock()
        with patch("supescrapper.fetcher.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session
            sc = Scraper()
            sc.fetch("https://example.com/1", dynamic=False)
            sc.fetch("https://example.com/2", dynamic=False)
            assert mock_session_cls.call_count == 1
            sc.close()
