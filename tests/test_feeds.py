"""Tests for RSS/Atom feed schema, XML parsing, and simultaneous multi-source feed scraping."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from supescrapper import (
    RSS_SCHEMA,
    AsyncScraper,
    Scraper,
    async_scrape_feeds,
    get_schema,
    load_config,
    parse_datetime,
    run_config,
    scrape_feeds,
    scrape_sources,
)
from supescrapper.parser import is_spa, is_xml, parse

SAMPLE_RSS_2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Tech Daily News</title>
    <link>https://techdaily.example/feed</link>
    <description>Daily technology updates</description>
    <item>
      <title>Autonomous Drones Advance</title>
      <link>https://techdaily.example/articles/101</link>
      <guid isPermaLink="true">https://techdaily.example/articles/101</guid>
      <description>Recent breakthroughs in drone autonomy and vision navigation.</description>
      <dc:creator>Sarah Connor</dc:creator>
      <pubDate>Wed, 21 Oct 2026 09:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Quantum Computing Milestones</title>
      <link>https://techdaily.example/articles/102</link>
      <guid isPermaLink="false">quantum-102</guid>
      <description>Researchers demonstrate fault-tolerant quantum error correction.</description>
      <dc:creator>Miles Dyson</dc:creator>
      <pubDate>Tue, 20 Oct 2026 14:15:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Global Insights</title>
  <link href="https://globalinsights.example/feed.atom" rel="self"/>
  <updated>2026-10-21T10:00:00Z</updated>
  <id>urn:uuid:60a76c80-d399-11d9-b91C-0003939e0af6</id>
  <entry>
    <title>Renewable Grid Transformation</title>
    <link href="https://globalinsights.example/posts/renewable-grid"/>
    <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
    <updated>2026-10-21T08:00:00Z</updated>
    <published>2026-10-21T07:30:00Z</published>
    <summary>A detailed review of regional microgrid modernization.</summary>
    <author>
      <name>Elena Rostova</name>
    </author>
  </entry>
</feed>
"""


class TestFeedXmlDetection:
    def test_is_xml_detection(self):
        assert is_xml(SAMPLE_RSS_2)
        assert is_xml(SAMPLE_ATOM)
        assert is_xml("<rss version='2.0'><channel></channel></rss>")
        assert is_xml("<feed xmlns='http://www.w3.org/2005/Atom'></feed>")
        assert not is_xml("<!DOCTYPE html><html><body><h1>Title</h1></body></html>")
        assert not is_xml("<p>Just text</p>")

    def test_xml_never_detected_as_spa(self):
        assert not is_spa(SAMPLE_RSS_2)
        assert not is_spa(SAMPLE_ATOM)

    def test_parse_uses_xml_parser(self):
        soup = parse(SAMPLE_RSS_2)
        # In XML mode, <link> inner text is preserved
        links = soup.find_all("link")
        assert any("techdaily.example" in l.get_text() for l in links)


class TestRfcDateTimeParsing:
    def test_parses_rfc822_gmt(self):
        iso = parse_datetime("Wed, 21 Oct 2026 09:30:00 GMT")
        assert "2026-10-21" in iso
        assert "09:30:00" in iso

    def test_parses_rfc822_offset(self):
        iso = parse_datetime("Tue, 20 Oct 2026 14:15:00 +0000")
        assert "2026-10-20" in iso
        assert "14:15:00" in iso


class TestRssSchemaExtraction:
    def test_extract_rss2(self):
        scraper = Scraper().from_html(SAMPLE_RSS_2)
        results = scraper.scrape("rss")

        assert len(results) == 2
        item1 = results[0]

        # Verify all requested fields
        assert item1["article_id"] == "https://techdaily.example/articles/101"
        assert item1["title"] == "Autonomous Drones Advance"
        assert "drone autonomy" in item1["description"]
        assert item1["source_urls"] == "https://techdaily.example/articles/101"
        assert item1["author_name"] == "Sarah Connor"
        assert "2026-10-21" in item1["date_published"]
        assert "time_scraped" in item1
        assert "scraped_at" in item1

        item2 = results[1]
        assert item2["article_id"] == "quantum-102"
        assert item2["title"] == "Quantum Computing Milestones"
        assert item2["author_name"] == "Miles Dyson"

    def test_extract_atom(self):
        scraper = Scraper().from_html(SAMPLE_ATOM)
        results = scraper.scrape("rss")

        assert len(results) == 1
        entry = results[0]
        assert entry["article_id"] == "urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a"
        assert entry["title"] == "Renewable Grid Transformation"
        assert "microgrid modernization" in entry["description"]
        assert entry["source_urls"] == "https://globalinsights.example/posts/renewable-grid"
        assert entry["author_name"] == "Elena Rostova"
        assert "2026-10-21" in entry["date_published"]
        assert "time_scraped" in entry

    def test_schema_aliases(self):
        assert get_schema("rss") is RSS_SCHEMA
        assert get_schema("feed").name == "rss"
        assert get_schema("news_feed").name == "rss"


class TestSimultaneousFeedScraping:
    def test_scrape_multiple_feeds_simultaneously(self):
        feeds = {
            "https://feed1.example/rss": SAMPLE_RSS_2,
            "https://feed2.example/atom": SAMPLE_ATOM,
        }

        def mock_fetch(url, *args, **kwargs):
            return feeds[url]

        with patch("supescrapper.scraper.fetch_static", side_effect=mock_fetch):
            results = scrape_feeds(
                ["https://feed1.example/rss", "https://feed2.example/atom"],
                concurrency=4,
            )

        assert len(results) == 3
        titles = {r["title"] for r in results}
        assert "Autonomous Drones Advance" in titles
        assert "Quantum Computing Milestones" in titles
        assert "Renewable Grid Transformation" in titles

        # Verify source_url identifies the feed
        feed_sources = {r["source_url"] for r in results}
        assert "https://feed1.example/rss" in feed_sources
        assert "https://feed2.example/atom" in feed_sources

    @pytest.mark.asyncio
    async def test_async_scrape_feeds_simultaneously(self):
        feeds = {
            "https://feed1.example/rss": SAMPLE_RSS_2,
            "https://feed2.example/atom": SAMPLE_ATOM,
        }

        async def mock_async_static(self, url, *args, **kwargs):
            return feeds[url]

        with patch.object(AsyncScraper, "_fetch_static", new=mock_async_static):
            results = await async_scrape_feeds(
                ["https://feed1.example/rss", "https://feed2.example/atom"],
                concurrency=2,
            )

        assert len(results) == 3
        titles = {r["title"] for r in results}
        assert "Autonomous Drones Advance" in titles
        assert "Renewable Grid Transformation" in titles

    def test_scraper_class_convenience_methods(self):
        with patch("supescrapper.feeds.scrape_feeds", return_value="ok") as mock_sf:
            res = Scraper.scrape_feeds(["https://example.com/rss"])
            assert res == "ok"
            mock_sf.assert_called_once()


class TestScrapeSources:
    RSS = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           '<item><title>Article One</title><link>https://ex.com/a1</link>'
           '<guid>https://ex.com/a1</guid><dc:creator>Alice</dc:creator>'
           '<pubDate>Wed, 21 Oct 2026 09:30:00 GMT</pubDate>'
           '<description>desc one</description></item>'
           '<item><title>Article Two</title><link>https://ex.com/a2</link>'
           '<guid>https://ex.com/a2</guid><dc:creator>Bob</dc:creator>'
           '<pubDate>Tue, 20 Oct 2026 14:15:00 +0000</pubDate>'
           '<description>desc two</description></item>'
           '</channel></rss>')

    def _mock_fetch(self, monkeypatch):
        feeds = {"https://ex.com/rss": self.RSS}

        def fake_fetch(url, timeout=30, retries=3):
            return feeds[url]

        monkeypatch.setattr("supescrapper.scraper.fetch_static", fake_fetch)

    def test_normalized_fields(self, monkeypatch):
        self._mock_fetch(monkeypatch)
        data = scrape_sources(["https://ex.com/rss"], per_source=5, schema="rss")
        assert len(data) == 2
        rec = data[0]
        assert set(rec.keys()) == {"source_url", "title", "description",
                                   "author", "time_published", "query"}
        assert rec["title"] == "Article One"
        assert rec["author"] == "Alice"
        assert rec["query"] == "rss"

    def test_per_source_limit(self, monkeypatch):
        self._mock_fetch(monkeypatch)
        data = scrape_sources(["https://ex.com/rss"], per_source=1, schema="rss")
        assert len(data) == 1

    def test_date_range_filter(self, monkeypatch):
        self._mock_fetch(monkeypatch)
        data = scrape_sources(
            ["https://ex.com/rss"], per_source=5, schema="rss",
            date_from="2026-10-21", date_to="2026-10-21",
        )
        assert len(data) == 1
        assert data[0]["title"] == "Article One"

    def test_export_csv(self, monkeypatch, tmp_path):
        self._mock_fetch(monkeypatch)
        data = scrape_sources(["https://ex.com/rss"], per_source=5, schema="rss")
        out = tmp_path / "news.csv"
        data.export("csv", out)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "Article One" in content
        assert "Alice" in content


class TestConfig:
    RSS = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           '<item><title>Article One</title><link>https://ex.com/a1</link>'
           '<guid>https://ex.com/a1</guid><dc:creator>Alice</dc:creator>'
           '<pubDate>Wed, 21 Oct 2026 09:30:00 GMT</pubDate>'
           '<description>desc one</description></item>'
           '</channel></rss>')

    def _write_config(self, tmp_path, extra=None):
        cfg = {
            "scrape": {
                "schema": "rss",
                "max_articles_per_source": 5,
                "date_from": "2026-10-21",
                "date_to": "2026-10-21",
                "export": "csv",
                "output": str(tmp_path / "news_cfg.csv"),
                "sources": [{"url": "https://ex.com/rss", "max_articles": 5}],
            }
        }
        if extra:
            cfg["scrape"].update(extra)
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return p

    def test_load_and_run_config(self, monkeypatch, tmp_path):
        feeds = {"https://ex.com/rss": self.RSS}

        def fake_fetch(url, timeout=30, retries=3):
            return feeds[url]

        monkeypatch.setattr("supescrapper.scraper.fetch_static", fake_fetch)

        p = self._write_config(tmp_path)
        cfg = load_config(p)
        assert len(cfg.sources) == 1
        assert cfg.schema == "rss"
        assert cfg.max_articles_per_source == 100
        assert cfg.sources[0].max_articles == 5

        results = run_config(cfg)
        assert len(results) == 1
        assert results[0]["title"] == "Article One"
        assert (tmp_path / "news_cfg.csv").exists()
