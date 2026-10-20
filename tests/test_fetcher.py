"""Tests for fetcher module (mocked HTTP)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from supescrapper.fetcher import fetch_static, _USER_AGENT
from supescrapper.exceptions import FetchError


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
