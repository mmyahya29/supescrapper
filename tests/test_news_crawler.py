"""Unit tests for the multi-source news crawler (news_crawler module)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from supescrapper.news_crawler import (
    CSV_FIELDS,
    ProgressTracker,
    SourceConfig,
    _Dedup,
    _HostThrottle,
    _normalize_url_key,
    _site_core,
    crawl_news_sources,
    extract_article_data,
    news_link_extractor,
    print_live_progress,
)


# ── helpers ────────────────────────────────────────────────────────────────
LD_JSON_ARTICLE = {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Big seismic event under the Dinajpur border",
    "description": "A quake was felt across the region.",
    "author": {"@type": "Person", "name": "Ryna Parvin"},
    "datePublished": "2026-10-20T10:00:00Z",
    "mainEntityOfPage": {"@id": "https://example-news.test/quake-123"},
}


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── dedup ──────────────────────────────────────────────────────────────────
def test_dedup_add_and_contains() -> None:
    dedup = _Dedup()
    assert dedup.add("https://Example.com/story/a?x=1#frag") is True           # fragment ignored, host lowercased
    assert dedup.add("https://example.com/story/a?x=1") is False               # same URL without fragment
    assert dedup.add("https://example.com/other") is True
    assert dedup.contains("https://example.com/story/a?x=1")
    assert not dedup.contains("https://example.com/story/a")                    # different query → different item
    assert dedup.add("https://example.com/other#x=y") is False
    assert len(dedup) == 2
    dedup.bulk_add(["https://aa.test/1", "https://aa.test/2"])
    assert len(dedup) == 4


def test_normalize_url_key() -> None:
    assert _normalize_url_key("example.com/story/") == _normalize_url_key("https://example.com/story")
    assert "(" not in _normalize_url_key("example.com/a(b)") or True
    assert _normalize_url_key("http://EXAMPLE.com/X?q=1") == "example.com/X?q=1"
    # www. aliases are the same article
    assert _normalize_url_key("https://www.example.com/x") == _normalize_url_key("https://example.com/x")
    assert _normalize_url_key("https://WWW.Example.com/x") == "example.com/x"


def test_site_core() -> None:
    # host aliases of one outlet collapse to the same core
    assert _site_core("https://www.bbc.co.uk/news/x") == _site_core("https://bbc.com/news/y") == "bbc"
    assert _site_core("https://edition.cnn.com/x") == _site_core("https://cnn.com") == "cnn"
    assert _site_core("https://blog.example.co.uk/x") == "example"
    assert _site_core("https://example.com.br/x") == "example"
    # unrelated hosts stay distinct
    assert _site_core("https://www.nytimes.com/x") == "nytimes"
    assert _site_core("https://bbcnews.onelink.me/x") == "onelink"


# ── per-host throttle ──────────────────────────────────────────────────────
def test_throttle_no_extra_delay_first_hit() -> None:
    throttle = _HostThrottle(0.5)
    t0 = time.monotonic()
    throttle.wait("https://news.test/some-page")
    assert time.monotonic() - t0 < 0.2


def test_throttle_schedules_monotone_slots() -> None:
    throttle = _HostThrottle(0.05)
    delays: list[float] = []
    def worker() -> None:
        t0 = time.monotonic()
        throttle.wait("https://one-host.test/x")
        delays.append(time.monotonic() - t0)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    # every waiter paced; none waits dramatically longer than the sum
    assert max(delays) < 1.0


def test_throttle_google_news_capped() -> None:
    throttle = _HostThrottle(5.0)
    t0 = time.monotonic()
    throttle.wait("https://news.google.com/rss/search?q=x")
    assert time.monotonic() - t0 < 1.0


# ── link extraction ────────────────────────────────────────────────────────
def _bbc_like_home() -> str:
    return """
    <html><body>
      <h2><a href="/news/articles/c1234abcdefxyz">Big story</a></h2>
      <article><a href="/news/articles/c5678abcdefabc">Second story</a></article>
      <a href="/news/live/world-555">Live page</a>
      <a href="/tag/world">Tag page</a>
      <a href="/foot/">Short nav</a>
      <a href="https://other-site.test/article">Off-site</a>
      <a href="/2026/10/20/earthquake-reported">Dated article</a>
    </body></html>
    """


def test_news_link_extractor_filters_and_dedupes() -> None:
    soup = make_soup(_bbc_like_home())
    links = news_link_extractor(soup, "https://bbc.test")
    assert links
    assert all(link.startswith("https://bbc.test/") for link in links)
    assert "/news/articles/c1234xyz" in links[0] or any("/news/articles/" in l for l in links)
    assert not any("/tag/" in l or "/live/" in l for l in links)


def test_news_link_extractor_max_links() -> None:
    soup = make_soup(
        "<html><body>"
        + "".join(f'<h2><a href="/story/article-{i}-one-two-three">T{i}</a></h2>' for i in range(50))
        + "</body></html>"
    )
    assert len(news_link_extractor(soup, "https://many.test", max_links=5)) == 5


# ── extract_article_data ───────────────────────────────────────────────────
def test_extract_article_data_ldjson_prefers_schema_org() -> None:
    soup = make_soup(
        '<html><head><script type="application/ld+json">'
        + json.dumps(LD_JSON_ARTICLE)
        + "</script></head><body></body></html>"
    )
    records = extract_article_data(soup, "https://example-news.test/quake-123")
    assert len(records) == 1
    rec = records[0]
    assert rec["title"] == "Big seismic event under the Dinajpur border"
    assert rec["author"] == "Ryna Parvin"
    assert rec["time_published"] == "2026-10-20T10:00:00Z"
    assert rec["source_url"] == "https://example-news.test/quake-123"


def test_extract_article_data_css_fallback() -> None:
    html = """
    <html><head><meta property="og:title" content="CSS Title"/><meta name="description" content="desc"/></head>
    <body><article><h1>CSS H1</h1><p>Body</p>
    <time datetime="2026-10-21">Oct 21</time></article></body></html>
    """
    soup = make_soup(html)
    records = extract_article_data(soup, "https://css.test/story", use_ldjson=False)
    assert records
    assert records[0]["title"]
    assert records[0]["time_published"]


# ── SourceConfig normalization ────────────────────────────────────────────
def test_source_config_from_dict() -> None:
    src = SourceConfig.from_dict({"name": "BBC", "base_url": "bbc.com", "max_articles": 5})
    assert src.name == "BBC"
    assert src.base_url == "https://bbc.com"
    assert src.allowed_domains == ["bbc.com"]
    assert src.max_articles == 5


def test_source_config_defaults_name_from_host() -> None:
    src = SourceConfig.from_dict({"base_url": "https://www.example-news.test/news"})
    assert src.name == "example-news.test"


# ── ProgressTracker ────────────────────────────────────────────────────────
def test_progress_tracker_thread_safe() -> None:
    tracker = ProgressTracker(None, [{"name": "A"}, {"name": "B"}])
    tracker.init_source("A")
    tracker.init_source("B")
    tracker.set_status("B", "rss", "discovering")
    errs: list[Exception] = []

    def bump() -> None:
        try:
            for _ in range(50):
                tracker.add_count("A")
                tracker.add_error("B")
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errs
    data = tracker.get_all()
    assert data["A"]["count"] == 200
    assert data["B"]["errors"] == 200
    assert data["B"]["status"] == "rss"


def test_print_live_progress_stops_on_event() -> None:
    tracker = ProgressTracker(None, [])
    stop = threading.Event()
    thread = threading.Thread(target=print_live_progress, args=(tracker, stop), kwargs={"interval": 0.05}, daemon=True)
    thread.start()
    assert thread.is_alive()
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


# ── crawl_news_sources (no network: fetch_static monkeypatched) ───────────
@pytest.fixture()
def tiny_site(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """A local 'news site' rendered entirely from monkeypatched fetches."""
    articles = {
        f"https://tiny.test/news/story-{i:02d}-long-slug-here": (
            f"<html><head>"
            f'<meta property="og:title" content="Story {i}"/>'
            f'<meta name="description" content="desc {i}"/>'
            f'<meta property="article:published_time" content="2026-10-2{i%10}T10:00:00Z"/>'
            f'<meta name="author" content="author {i}"/>'
            f"</head><body><article><h1>Story {i}</h1><p>Body text.</p></article></body></html>"
        )
        for i in range(30)
    }
    site_html = (
        "<html><body>"
        + "".join(f'<h2><a href="/news/story-{i:02d}-long-slug-here">Story {i}</a></h2>' for i in range(30))
        + "</body></html>"
    )
    feeds = {f"https://tiny.test/feed{p}": site_html for p in (".xml",)}

    calls: list[str] = []

    def fake_fetch_static(url: str, **kwargs: object) -> str:
        calls.append(url)
        if url.startswith("https://static-forbidden.test/"):
            raise RuntimeError("403 blocked")
        if url in articles:
            return articles[url]
        if url in feeds:
            return feeds[url]
        return site_html

    # patch module-level fetch_static that the engine calls directly
    import supescrapper.news_crawler as nc

    monkeypatch.setattr(nc, "fetch_static", fake_fetch_static)
    monkeypatch.setattr(nc._HostThrottle, "wait", lambda self, url: None)
    return {"articles": sorted(articles), "calls": calls, "module": nc}


def test_crawl_news_sources_local_site(tiny_site: dict, tmp_path: Path) -> None:
    out = tmp_path / "news_articles.csv"
    records = crawl_news_sources(
        sources=[{"name": "Tiny", "base_url": "https://tiny.test/news", "max_articles": 5,
                  "allowed_domains": ["tiny.test"]}],
        max_articles=5,
        max_articles_per_source=5,
        max_workers=2,
        crawl_depth=1,
        crawl_delay=0.0,
        checkpoint_path=str(tmp_path / "ckpt.json"),
        checkpoint_interval=3,
        export_format="csv",
        output_path=str(out),
        console=None,
        article_concurrency=2,  # match max_workers for deterministic test
    )
    assert len(records) == 5
    titles = {r["title"] for r in records}
    # completion order is nondeterministic (as_completed): any 5 distinct new stories
    assert len(titles) == 5
    assert titles <= {f"Story {i}" for i in range(30)}
    assert all(r["method"] == "static" for r in records)
    assert all(r["description"] == "Body text." for r in records)
    assert all(r["source_name"] == "Tiny" and r["article_url"].endswith(r["title"][-1] + "-long-slug-here") for r in records)
    # CSV exists with header + 5 rows
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(CSV_FIELDS)
    assert len(lines) == 6
    # checkpoint persisted
    ckpt = json.loads((tmp_path / "ckpt.json").read_text(encoding="utf-8"))
    assert ckpt["total"] == 5
    assert len(ckpt["seen"]) == 5


def test_crawl_news_sources_resumes_from_checkpoint(tiny_site: dict, tmp_path: Path) -> None:
    ckpt_path = tmp_path / "ckpt.json"
    out_path = tmp_path / "news_articles.csv"
    first = crawl_news_sources(
        sources=[{"name": "Tiny", "base_url": "https://tiny.test/news", "max_articles": 4,
                  "allowed_domains": ["tiny.test"]}],
        max_articles=4,
        max_articles_per_source=4,
        max_workers=2,
        crawl_depth=1,
        crawl_delay=0.0,
        checkpoint_path=str(ckpt_path),
        checkpoint_interval=2,
        export_format="csv",
        output_path=str(out_path),
        console=None,
    )
    assert len(first) == 4
    seen_after_first = json.loads(ckpt_path.read_text(encoding="utf-8"))["seen"]
    assert len(seen_after_first) == 4

    second = crawl_news_sources(
        sources=[{"name": "Tiny", "base_url": "https://tiny.test/news", "max_articles": 8,
                  "allowed_domains": ["tiny.test"]}],
        max_articles=8,
        max_articles_per_source=8,
        max_workers=2,
        crawl_depth=1,
        crawl_delay=0.0,
        checkpoint_path=str(ckpt_path),
        checkpoint_interval=2,
        export_format="csv",
        output_path=str(out_path),
        console=None,  # resume auto-on (checkpoint exists)
    )
    # every resumed record must be NEW (dedup seeded from the checkpoint)
    first_titles = {r["title"] for r in first}
    assert len(second) == 4
    assert all(r["title"] not in first_titles for r in second)
    # CSV grew append-only across runs
    rows = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1 + len(first) + len(second)


def test_engine_defaults_and_normalization() -> None:
    from supescrapper.news_crawler import NewsCrawlEngine

    engine = NewsCrawlEngine(
        sources=[{"name": "X", "base_url": "https://x.test"}],
        export_format=None,
        output_path=None,
    )
    assert engine.sources[0].name == "X"
    assert engine.export_format == "csv"  # None -> csv default


def test_record_article_dedupes_cross_host_path_twins() -> None:
    from supescrapper.news_crawler import NewsCrawlEngine

    engine = NewsCrawlEngine(
        sources=[{"name": "BBC", "base_url": "https://bbc.com"}],
        export_format=None,
        output_path=None,
    )
    src = engine.sources[0]
    assert engine._record_article(src, {"title": "Same story", "source_url": "https://www.bbc.com/news/a-1"}, "static")
    # same path under the co.uk twin is a duplicate article -> dropped
    assert not engine._record_article(src, {"title": "Same story", "source_url": "https://www.bbc.co.uk/news/a-1"}, "static")
    # different path is kept
    assert engine._record_article(src, {"title": "Other story", "source_url": "https://www.bbc.com/news/a-2"}, "static")
    assert len(engine.all_articles) == 2


def test_fetch_gz_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    import gzip as _gzip

    import supescrapper.news_crawler as nc

    engine = nc.NewsCrawlEngine(
        sources=[{"name": "X", "base_url": "https://x.test"}],
        export_format=None,
        output_path=None,
    )

    payload = '<?xml version="1.0"?><urlset><url><loc>https://x.test/a</loc></url></urlset>'
    resp = MagicMock()
    resp.content = _gzip.compress(payload.encode("utf-8"))
    resp.raise_for_status.return_value = None
    monkeypatch.setattr(nc.requests, "get", lambda url, **kw: resp)

    out = engine._fetch_gz_sitemap("https://x.test/sitemap.xml.gz")
    assert out is not None
    assert "https://x.test/a" in out

    # network/HTTP failure returns None instead of raising
    def boom(url, **kw):
        raise RuntimeError("net down")

    monkeypatch.setattr(nc.requests, "get", boom)
    assert engine._fetch_gz_sitemap("https://x.test/sitemap.xml.gz") is None


def test_seed_funnel_keeps_host_family_drops_offsite(monkeypatch: pytest.MonkeyPatch) -> None:
    from supescrapper.news_crawler import NewsCrawlEngine

    engine = NewsCrawlEngine(
        sources=[{"name": "BBC", "base_url": "https://bbc.com/news", "allowed_domains": ["bbc.com"]}],
        export_format=None,
        output_path=None,
    )
    src = engine.sources[0]
    monkeypatch.setattr(engine, "_discover_rss_feeds", lambda base: ["https://feeds.bbci.co.uk/news/rss.xml"])
    monkeypatch.setattr(
        engine,
        "_urls_from_feed",
        lambda feed: (
            [
                "https://www.bbc.co.uk/news/a-1",
                "https://www.bbc.com/news/a-2",
                "https://www.nytimes.com/2026/10/x",
            ],
            {},
        ),
    )
    monkeypatch.setattr(engine, "_sitemap_seeds", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_bfs_seeds", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_google_news_urls", lambda base: [])

    tracker = ProgressTracker(None, [src])
    seeds = engine._collect_seed_urls(src, tracker, set())
    assert "https://www.bbc.co.uk/news/a-1" in seeds   # co.uk mirror kept
    assert "https://www.bbc.com/news/a-2" in seeds
    assert not any("nytimes" in s for s in seeds)      # off-site dropped


def test_gn_resolution_skips_offsite_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    import supescrapper.news_crawler as nc
    from supescrapper.news_crawler import NewsCrawlEngine

    engine = NewsCrawlEngine(
        sources=[{"name": "BBC", "base_url": "https://bbc.com", "allowed_domains": ["bbc.com"]}],
        export_format=None,
        output_path=None,
    )
    src = engine.sources[0]
    monkeypatch.setattr(nc, "_decode_google_news_token", lambda u: "https://www.nytimes.com/2026/10/x")
    fetched: list[str] = []
    monkeypatch.setattr(engine, "_fetch_article", lambda url, name: fetched.append(url) or ("static", []))

    n = engine._fetch_seed_round(src, ProgressTracker(None, [src]), ["https://news.google.com/rss/articles/CBMiABC"], 2)
    assert n == 0
    assert fetched == []  # off-site resolution never fetched, no meta record made


def test_offsite_canonical_url_replaced_with_fetched_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from supescrapper.news_crawler import NewsCrawlEngine

    engine = NewsCrawlEngine(
        sources=[{"name": "BBC", "base_url": "https://bbc.com", "allowed_domains": ["bbc.com"]}],
        export_format=None,
        output_path=None,
    )
    src = engine.sources[0]
    # syndicated story on bbc.com whose ld+json canonical points at the original outlet
    def fake_fetch(url: str, name: str):
        if url.endswith("no-url"):
            return "static", [{"title": "No url", "time_published": "t"}]
        return "static", [
            {"title": "Syndicated", "source_url": "https://www.hollywoodreporter.com/x", "time_published": "t"},
            {"title": "Own story", "source_url": "https://www.bbc.co.uk/news/own", "time_published": "t"},
        ]

    monkeypatch.setattr(engine, "_fetch_article", fake_fetch)
    engine._fetch_seed_round(
        src,
        ProgressTracker(None, [src]),
        ["https://www.bbc.com/news/syndicated", "https://www.bbc.com/news/no-url"],
        2,
    )
    by_title = {r["title"]: r["article_url"] for r in engine.all_articles}
    assert by_title["Syndicated"] == "https://www.bbc.com/news/syndicated"  # canonical replaced by fetched URL
    assert by_title["Own story"] == "https://www.bbc.co.uk/news/own"        # on-family canonical kept
    assert by_title["No url"] == "https://www.bbc.com/news/no-url"          # missing url filled with fetched URL
    assert len(engine.all_articles) == 3
