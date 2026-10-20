"""Tests for CLI module."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from supescrapper.cli import main, _build_parser


class TestBuildParser:
    def test_has_all_flags(self):
        parser = _build_parser()
        args = parser.parse_args(["https://example.com", "--select", "h1", "--table"])
        assert args.url == "https://example.com"
        assert args.select == "h1"
        assert args.table is True

    def test_find_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["https://example.com", "--find", "button"])
        assert args.find == "button"

    def test_classify_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["https://example.com", "--select", "div", "--classify"])
        assert args.classify is True

    def test_find_interactive_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["https://example.com", "--find-interactive"])
        assert args.find_interactive is True

    def test_list_types_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--list-types"])
        assert args.list_types is True

    def test_static_dynamic_flags(self):
        parser = _build_parser()
        args = parser.parse_args(["https://example.com", "--static"])
        assert args.static is True
        args2 = parser.parse_args(["https://example.com", "--dynamic"])
        assert args2.dynamic is True


class TestMain:
    def test_no_url_no_html_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_list_types_exits_cleanly(self):
        with patch("supescrapper.cli.Console") as mock_console_cls:
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            main(["--list-types"])
            mock_console.print.assert_called()

    def test_find_calls_scraper(self):
        with patch("supescrapper.cli.Scraper") as mock_scraper_cls:
            mock_scraper = MagicMock()
            mock_results = MagicMock()
            mock_results.__len__ = lambda self: 1
            mock_results.__bool__ = lambda self: True
            mock_scraper.fetch.return_value = mock_scraper
            mock_scraper.find_elements.return_value = mock_results
            mock_scraper_cls.return_value = mock_scraper

            with patch("supescrapper.cli.Console"):
                main(["https://example.com", "--find", "button"])

            mock_scraper.find_elements.assert_called_once_with("button")

    def test_interactive_calls_scraper(self):
        with patch("supescrapper.cli.Scraper") as mock_scraper_cls:
            mock_scraper = MagicMock()
            mock_results = MagicMock()
            mock_results.__len__ = lambda self: 1
            mock_results.__bool__ = lambda self: True
            mock_scraper.fetch.return_value = mock_scraper
            mock_scraper.find_interactive.return_value = mock_results
            mock_scraper_cls.return_value = mock_scraper

            with patch("supescrapper.cli.Console"):
                main(["https://example.com", "--find-interactive"])

            mock_scraper.find_interactive.assert_called_once()

    def test_classify_calls_scraper(self):
        with patch("supescrapper.cli.Scraper") as mock_scraper_cls:
            mock_scraper = MagicMock()
            mock_results = MagicMock()
            mock_results.__len__ = lambda self: 1
            mock_results.__bool__ = lambda self: True
            mock_scraper.fetch.return_value = mock_scraper
            mock_scraper.query.return_value = mock_scraper
            mock_scraper.classify.return_value = mock_scraper
            mock_scraper.extract.return_value = mock_results
            mock_scraper_cls.return_value = mock_scraper

            with patch("supescrapper.cli.Console"):
                main(["https://example.com", "--select", "div", "--classify"])

            mock_scraper.classify.assert_called_once()
