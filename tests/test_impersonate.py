"""Tests for the Level 3 impersonation backend and proxy pool."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from supescrapper.impersonate import (
    ImpersonatingFetcher,
    ProxyPool,
    ImpersonateNotInstalledError,
    status_is_retryable,
)
from supescrapper.exceptions import FetchError


class TestProxyPool:
    def test_empty(self):
        pool = ProxyPool([])
        assert len(pool) == 0
        assert not pool
        assert pool.next() is None

    def test_round_robin(self):
        pool = ProxyPool(["a", "b", "c"])
        assert pool.next() == "a"
        assert pool.next() == "b"
        assert pool.next() == "c"
        assert pool.next() == "a"

    def test_random_returns_member(self):
        pool = ProxyPool(["a", "b"], strategy="random")
        assert pool.next() in ("a", "b")

    def test_invalid_strategy(self):
        with pytest.raises(ValueError):
            ProxyPool(["a"], strategy="nonsense")

    def test_filters_empty_entries(self):
        pool = ProxyPool(["a", "", None])  # type: ignore[list-item]
        assert len(pool) == 1


class TestStatusIsRetryable:
    def test_retryable(self):
        assert status_is_retryable(Exception("HTTP 429 for url"))
        assert status_is_retryable(Exception("HTTP 503"))

    def test_not_retryable(self):
        assert not status_is_retryable(Exception("HTTP 404"))


class TestImpersonatingFetcher:
    def _fake_session(self, status=200, text="<html>ok</html>"):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        session = MagicMock()
        session.get.return_value = resp
        return session

    def test_fetch_success(self):
        session = self._fake_session(text="<h1>hi</h1>")
        with patch("supescrapper.impersonate._require_curl_cffi") as req:
            req.return_value.Session.return_value = session
            fetcher = ImpersonatingFetcher(impersonate="chrome124", retries=1)
            html = fetcher.fetch("example.com")
        assert html == "<h1>hi</h1>"
        assert req.return_value.Session.call_args.kwargs["impersonate"] == "chrome124"

    def test_http_error_raises_fetch_error(self):
        session = self._fake_session(status=403)
        with patch("supescrapper.impersonate._require_curl_cffi") as req:
            req.return_value.Session.return_value = session
            fetcher = ImpersonatingFetcher(retries=2, backoff=0)
            with pytest.raises(FetchError, match="after 2 attempts"):
                fetcher.fetch("example.com")

    def test_retries_on_failure(self):
        session = MagicMock()
        session.get.side_effect = RuntimeError("tls fail")
        with patch("supescrapper.impersonate._require_curl_cffi") as req:
            req.return_value.Session.return_value = session
            fetcher = ImpersonatingFetcher(retries=3, backoff=0)
            with pytest.raises(FetchError):
                fetcher.fetch("example.com")
        assert session.get.call_count == 3

    def test_proxy_rotation_used(self):
        session = self._fake_session()
        with patch("supescrapper.impersonate._require_curl_cffi") as req:
            req.return_value.Session.return_value = session
            fetcher = ImpersonatingFetcher(proxies=["http://p1", "http://p2"], retries=1)
            fetcher.fetch("example.com")
        call = session.get.call_args
        assert call.kwargs["proxies"] == {"http": "http://p1", "https": "http://p1"}

    def test_missing_curl_cffi_raises_clear_error(self):
        with patch("supescrapper.impersonate._require_curl_cffi",
                   side_effect=ImpersonateNotInstalledError("install me")):
            fetcher = ImpersonatingFetcher(retries=1)
            with pytest.raises(ImpersonateNotInstalledError):
                fetcher.fetch("example.com")


class TestScraperImpersonateWiring:
    def test_scraper_fetch_with_impersonate(self):
        from supescrapper import Scraper
        page = '<html><body><h1>Hello</h1><p>' + ("word " * 100) + '</p></body></html>'
        with patch("supescrapper.impersonate.ImpersonatingFetcher.fetch", return_value=page):
            res = Scraper().fetch("https://example.com", impersonate="chrome124").query("h1").extract()
        assert res[0].text == "Hello"

    def test_scraper_impersonate_true_uses_default(self):
        from supescrapper import Scraper
        page = '<html><body><h1>X</h1><p>' + ("word " * 100) + '</p></body></html>'
        captured = {}

        def fake_fetch(self, url, **kwargs):
            captured["impersonate"] = self.impersonate
            return page

        with patch("supescrapper.impersonate.ImpersonatingFetcher.fetch", fake_fetch):
            Scraper().fetch("https://example.com", impersonate=True).query("h1").extract()
        assert captured["impersonate"] == "chrome124"
