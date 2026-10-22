"""Simultaneous scraping of multiple RSS/Atom feeds.

    from supescrapper import scrape_feeds

    records = scrape_feeds(
        ["https://example.com/rss", "https://example.com/atom"],
        concurrency=4,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

from . import scraper as _scraper_mod
from .crawler import CrawlConfig, CrawlEngine
from .parser import parse
from .schema import Schema, SchemaResultSet, get_schema

_log = logging.getLogger("supescrapper")


def _scrape_one(
    url: str,
    schema: Schema,
    *,
    strict: bool,
    timeout: int,
    retries: int,
) -> list[dict[str, Any]]:
    html = _scraper_mod.fetch_static(url, timeout=timeout, retries=retries)
    soup = parse(html)
    return schema.extract(soup, base_url=url, strict=strict)


def scrape_feeds(
    urls: list[str],
    *,
    concurrency: int = 8,
    schema: str | Schema = "rss",
    strict: bool = False,
    timeout: int = 30,
    retries: int = 3,
) -> SchemaResultSet:
    """Fetch every URL in *urls* concurrently and extract records with *schema*.

    Returns one flat list of records; every record carries the feed URL under
    ``source_url`` plus ``scraped_at``/``time_scraped``. One failing feed never
    kills the whole batch — its error is logged and skipped.
    """
    sch = get_schema(schema)
    results: list[dict[str, Any]] = []

    def job(url: str) -> list[dict[str, Any]]:
        try:
            return _scrape_one(url, sch, strict=strict, timeout=timeout, retries=retries)
        except Exception as exc:  # noqa: BLE001  # one bad feed must not kill the whole batch
            _log.warning("feed %s failed: %s", url, exc)
            return []

    workers = max(1, concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, url): url for url in urls}
        for fut in as_completed(futures):
            results.extend(fut.result() or [])
    return SchemaResultSet(results)


async def async_scrape_feeds(
    urls: list[str],
    *,
    concurrency: int = 8,
    schema: str | Schema = "rss",
    strict: bool = False,
    timeout: int = 30,
    retries: int = 3,
) -> SchemaResultSet:
    """Asyncio twin of :func:`scrape_feeds`."""
    from .async_scraper import AsyncScraper

    sch = get_schema(schema)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def job(url: str) -> list[dict[str, Any]]:
        async with sem:
            scraper = AsyncScraper()
            html = await scraper._fetch_static(url, timeout=timeout, retries=retries)
            soup = parse(html)
            return sch.extract(soup, base_url=url, strict=strict)

    per_feed = await asyncio.gather(*(job(u) for u in urls))
    results: list[dict[str, Any]] = []
    for batch in per_feed:
        results.extend(batch or [])
    return SchemaResultSet(results)


# --- config-driven multi-source scraping -------------------------------------

_FIELD_ALIASES = {
    "source_url": ("source_url", "url", "link"),
    "title": ("title", "name", "headline"),
    "description": ("description", "summary", "excerpt"),
    "author": ("author_name", "author", "byline", "writer"),
    "time_published": ("date_published", "published_at", "published", "updated_at"),
}


def _pick(record: dict[str, Any], names: tuple[str, ...]) -> Any:
    for n in names:
        if n in record and record[n] not in (None, ""):
            return record[n]
    return None


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _in_range(iso: str | None, date_from: date | None, date_to: date | None) -> bool:
    if date_from is None and date_to is None:
        return True
    d = _parse_date(iso or "")
    if d is None:
        return True  # keep records we can't date
    if date_from is not None and d < date_from:
        return False
    if date_to is not None and d > date_to:
        return False
    return True


def normalize_record(record: dict[str, Any], query: str) -> dict[str, Any]:
    """Map a schema record to the standard news-output fields."""
    return {
        "source_url": record.get("source_url") or record.get("url") or "",
        "title": _pick(record, _FIELD_ALIASES["title"]),
        "description": _pick(record, _FIELD_ALIASES["description"]),
        "author": _pick(record, _FIELD_ALIASES["author"]),
        "time_published": _pick(record, _FIELD_ALIASES["time_published"]),
        "query": query,
    }


@dataclass
class SourceConfig:
    url: str
    schema: str = "news"
    max_articles: int = 100
    date_from: str | None = None
    date_to: str | None = None
    dynamic: bool | str = "auto"
    crawl: bool = False
    follow_selector: str | None = None
    max_depth: int | None = None
    max_urls: int | None = None


@dataclass
class ScrapeConfig:
    sources: list[SourceConfig] = dc_field(default_factory=list)
    export: str | None = None
    output: str | None = None
    concurrency: int = 8
    timeout: int = 30
    retries: int = 3
    date_from: str | None = None
    date_to: str | None = None
    schema: str = "news"
    strict: bool = False
    max_articles_per_source: int = 100


def _config_date(value: str | None) -> date | None:
    return _parse_date(value) if value else None


def load_config(path: str | Path) -> ScrapeConfig:
    """Load a scrape config from JSON or TOML.

    Example (TOML):

        [scrape]
        export = "csv"
        output = "news.csv"
        concurrency = 4
        schema = "news"
        max_articles_per_source = 100
        date_from = "2026-10-01"
        date_to = "2026-10-21"

        [[sources]]
        url = "https://example.com/news"
        schema = "news"
        max_articles = 100
        crawl = true
        follow_selector = "a.next"
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    if suffix == ".toml":
        if tomllib is None:  # pragma: no cover
            raise ImportError("tomllib is required to read TOML configs (Python 3.11+)")
        data = tomllib.loads(text)
    else:
        data = json.loads(text)

    sc = data.get("scrape", data)
    sources = []
    for s in sc.get("sources", []):
        sources.append(SourceConfig(
            url=s["url"],
            schema=s.get("schema", sc.get("schema", "news")),
            max_articles=int(s.get("max_articles", sc.get("max_articles_per_source", 100))),
            date_from=s.get("date_from", sc.get("date_from")),
            date_to=s.get("date_to", sc.get("date_to")),
            dynamic=s.get("dynamic", "auto"),
            crawl=bool(s.get("crawl", False)),
            follow_selector=s.get("follow_selector"),
            max_depth=s.get("max_depth"),
            max_urls=s.get("max_urls"),
        ))
    return ScrapeConfig(
        sources=sources,
        export=sc.get("export"),
        output=sc.get("output"),
        concurrency=int(sc.get("concurrency", 8)),
        timeout=int(sc.get("timeout", 30)),
        retries=int(sc.get("retries", 3)),
        date_from=sc.get("date_from"),
        date_to=sc.get("date_to"),
        schema=sc.get("schema", "news"),
        strict=bool(sc.get("strict", False)),
    )


def _scrape_source(source: SourceConfig, cfg: ScrapeConfig) -> list[dict[str, Any]]:
    """Scrape a single source, honoring max_articles and date range."""

    sch = get_schema(source.schema)
    date_from = _config_date(source.date_from or cfg.date_from)
    date_to = _config_date(source.date_to or cfg.date_to)
    collected: list[dict[str, Any]] = []

    if source.crawl:
        cfg_crawl = CrawlConfig(
            max_urls=source.max_urls or source.max_articles,
            concurrency=cfg.concurrency,
            timeout=cfg.timeout,
            retries=cfg.retries,
            max_depth=source.max_depth,
            follow_selector=source.follow_selector,
            dynamic=source.dynamic if source.dynamic != "auto" else False,
            schema=source.schema,
            strict_schema=cfg.strict,
        )
        engine = CrawlEngine(cfg_crawl)
        results = engine.crawl(source.url)
        for cr in results:
            for it in cr.items:
                rec = dict(it.meta)
                rec["source_url"] = it.url or source.url
                if not _in_range(rec.get("date_published") or rec.get("published_at"), date_from, date_to):
                    continue
                collected.append(normalize_record(rec, source.schema))
                if len(collected) >= source.max_articles:
                    return collected
        return collected

    html = _scraper_mod.fetch_static(
        source.url,
        timeout=cfg.timeout,
        retries=cfg.retries,
    )
    soup = parse(html)
    records = sch.extract(soup, base_url=source.url, strict=cfg.strict)
    for rec in records:
        if not _in_range(rec.get("date_published") or rec.get("published_at"), date_from, date_to):
            continue
        collected.append(normalize_record(rec, source.schema))
        if len(collected) >= source.max_articles:
            break
    return collected


def scrape_sources(
    sources: list[str | SourceConfig],
    *,
    per_source: int = 100,
    schema: str = "news",
    date_from: str | None = None,
    date_to: str | None = None,
    concurrency: int = 8,
    export: str | None = None,
    output: str | None = None,
    **kwargs: Any,
) -> SchemaResultSet:
    """Scrape multiple news sources and return normalized records.

    Each record has: source_url, title, description, author, time_published,
    query (the schema used).

    Examples::

        from supescrapper import scrape_sources

        data = scrape_sources(
            ["https://example.com/news", "https://example.org/feed"],
            per_source=100,
            date_from="2026-10-01",
            date_to="2026-10-21",
        )
        data.export("csv", "news.csv")
    """
    srcs: list[SourceConfig] = []
    for s in sources:
        if isinstance(s, SourceConfig):
            srcs.append(s)
        else:
            srcs.append(SourceConfig(
                url=s,
                schema=schema,
                max_articles=per_source,
                date_from=date_from,
                date_to=date_to,
            ))

    cfg = ScrapeConfig(
        sources=srcs,
        export=export,
        output=output,
        concurrency=concurrency,
        date_from=date_from,
        date_to=date_to,
        schema=schema,
        **kwargs,
    )
    return run_config(cfg)


def run_config(cfg: ScrapeConfig) -> SchemaResultSet:
    """Run a :class:`ScrapeConfig` and return normalized records.

    Applies per-source limits, date-range filtering, optional crawling, and
    optional export (JSON / CSV / SQLite).
    """
    results: list[dict[str, Any]] = []

    def job(source: SourceConfig) -> list[dict[str, Any]]:
        try:
            return _scrape_source(source, cfg)
        except Exception as exc:  # noqa: BLE001  # one source must not kill the batch
            _log.warning("source %s failed: %s", source.url, exc)
            return []

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
        futures = {pool.submit(job, s): s for s in cfg.sources}
        for fut in as_completed(futures):
            results.extend(fut.result() or [])

    rs = SchemaResultSet(results)
    if cfg.export and cfg.output:
        rs.export(cfg.export, cfg.output)
    return rs
