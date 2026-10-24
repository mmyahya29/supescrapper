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


class TestCliIntegration:
    """End-to-end CLI runs against a local HTML file (real Scraper)."""

    def _html_file(self, tmp_path):
        p = tmp_path / "page.html"
        p.write_text(
            '<html><body>'
            '<button>OK</button><button>Cancel</button>'
            '<a href="/blog">Blog</a>'
            '</body></html>',
            encoding="utf-8",
        )
        return str(p)

    def test_find_does_not_crash(self, tmp_path):
        with patch("supescrapper.cli.Console") as mock_console_cls:
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            main(["--html-file", self._html_file(tmp_path), "--find", "button"])
            mock_console.print.assert_called()

    def test_find_with_filter(self, tmp_path):
        with patch("supescrapper.cli.Console") as mock_console_cls:
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            main([
                "--html-file", self._html_file(tmp_path),
                "--find", "button",
                "--where-text", "contains", "OK",
            ])
            printed = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "1 results found" in printed

    def test_selector_error_is_handled(self, tmp_path):
        with patch("supescrapper.cli.Console") as mock_console_cls:
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            with pytest.raises(SystemExit) as exc:
                main(["--html-file", self._html_file(tmp_path), "--select", "nonexistent-tag"])
            assert exc.value.code == 1

    def test_xpath_selection(self, tmp_path):
        with patch("supescrapper.cli.Console") as mock_console_cls:
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            main(["--html-file", self._html_file(tmp_path), "--select-xpath", "//button"])
            printed = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "2 results found" in printed


class TestCliCrawl:
    def test_crawl_requires_url(self):
        with patch("supescrapper.cli.Console"):
            with pytest.raises(SystemExit):
                main(["--crawl"])

    def test_crawl_runs_with_injected_fetch(self, tmp_path):
        site = {
            "https://s.test/": '<html><body><a href="/a">A</a></body></html>',
            "https://s.test/a": '<html><body><p>leaf</p></body></html>',
        }

        def fake_fetch(url, config):
            return site[url]

        with patch("supescrapper.crawler._default_fetch", fake_fetch):
            with patch("supescrapper.cli.Console") as mock_console_cls:
                mock_console = MagicMock()
                mock_console_cls.return_value = mock_console
                main(["https://s.test/", "--crawl", "--select", "a", "--max-urls", "5"])
                printed = " ".join(str(c) for c in mock_console.print.call_args_list)
                assert "Crawled" in printed

    def test_crawl_export(self, tmp_path):
        site = {"https://s.test/": '<html><body><a href="/a">A</a></body></html>'}
        out = tmp_path / "crawl.json"

        def fake_fetch(url, config):
            return site.get(url, "<p>x</p>")

        with patch("supescrapper.crawler._default_fetch", fake_fetch):
            with patch("supescrapper.cli.Console"):
                main(["https://s.test/", "--crawl", "--select", "a",
                      "--export", "json", "-o", str(out)])
        assert out.exists()


class TestCliFeeds:
    def test_feeds_calls_scrape_feeds(self):
        with patch("supescrapper.feeds.scrape_feeds") as mock_sf:
            from supescrapper.schema import SchemaResultSet
            mock_sf.return_value = SchemaResultSet([])
            with patch("supescrapper.cli.Console"):
                main(["--feeds", "https://a.test/rss", "https://b.test/atom", "--schema", "rss"])
            mock_sf.assert_called_once()
            call_args = mock_sf.call_args
            assert call_args[0][0] == ["https://a.test/rss", "https://b.test/atom"]
            assert call_args[1]["schema"] == "rss"


class TestNewsCrawlCli:
    def test_single_url_crawl_uses_url_as_source(self, tmp_path):
        fake_records = [{"title": "T", "source_url": "u"}]
        with patch("supescrapper.news_crawler.crawl_news_sources", return_value=fake_records) as mock_crawl:
            with patch("supescrapper.cli.Console"):
                main([
                    "https://bbc.com/news",
                    "--news", "--crawl",
                    "--target", "50",
                    "--export", "csv",
                    "-o", str(tmp_path / "out.csv"),
                    "--checkpoint", str(tmp_path / "ckpt.json"),
                ])
        kwargs = mock_crawl.call_args.kwargs
        assert kwargs["sources"] == [
            {"name": "bbc.com", "base_url": "https://bbc.com/news", "allowed_domains": ["bbc.com"]}
        ]
        assert kwargs["max_articles"] == 50
        assert kwargs["max_articles_per_source"] == 50

    def test_sources_split_target_across_sources(self, tmp_path):
        with patch("supescrapper.news_crawler.crawl_news_sources", return_value=[]) as mock_crawl:
            with patch("supescrapper.cli.Console"):
                main([
                    "--news", "--crawl",
                    "--sources", "https://a.test", "https://b.test",
                    "--target", "100",
                    "--export", "csv",
                    "-o", str(tmp_path / "out.csv"),
                    "--checkpoint", str(tmp_path / "ckpt.json"),
                ])
        kwargs = mock_crawl.call_args.kwargs
        assert len(kwargs["sources"]) == 2
        assert kwargs["max_articles"] == 100
        assert kwargs["max_articles_per_source"] == 50

    def test_crawl_without_url_or_sources_exits(self):
        with patch("supescrapper.cli.Console"):
            with pytest.raises(SystemExit):
                main(["--news", "--crawl"])

