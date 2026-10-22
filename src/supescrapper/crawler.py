"""Level 4: a small concurrent crawl engine.

Provides a lightweight Scrapy-like workflow on top of the existing fetch
backends:

  * BFS frontier with a bounded worker pool
  * crawl-time URL deduplication (normalized, fragment-stripped)
  * per-domain politeness delay + optional adaptive throttling
  * retry middleware with exponential backoff
  * proxy rotation (via ProxyPool)
  * item pipeline hooks (callables applied to each scraped item)
  * optional pagination following (CSS selector -> next URL)

Static crawling uses a thread pool; ``AsyncCrawlEngine`` uses asyncio tasks.
Both share :class:`CrawlConfig` and the item/pipeline machinery.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Sequence
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from .exceptions import SupescrapperError
from .impersonate import ProxyPool
from .models import ScrapedItem
from .parser import parse
from .schema import Schema, get_schema

_log = logging.getLogger("supescrapper")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class CrawlConfig:
    """Configuration for a crawl run."""

    max_urls: int = 1000
    concurrency: int = 8
    download_delay: float = 0.0          # seconds between requests to the same host
    autothrottle: bool = False           # adapt delay based on server latency/errors
    retries: int = 3
    timeout: int = 30
    max_depth: int | None = None
    same_domain_only: bool = True
    respect_robots: bool = False         # see docs; robots.txt is a user responsibility
    user_agent: str = DEFAULT_USER_AGENT
    headers: dict[str, str] = field(default_factory=dict)
    impersonate: str | bool | None = None
    proxy_pool: list[str] = field(default_factory=list)
    proxy_strategy: str = "round_robin"
    allowed_domains: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)   # regex, skip matching URLs
    allow_patterns: list[str] = field(default_factory=list)  # regex, only URLs matching any
    dynamic: bool | str = False
    follow_selector: str | None = None    # CSS selector for "next page" links
    schema: str | Schema | None = None   # structured extraction via schema
    strict_schema: bool = False
    system_fields: Sequence[str] | None = None


@dataclass
class CrawlResult:
    url: str
    status: str                          # "ok" | "error"
    items: list[ScrapedItem] = field(default_factory=list)
    error: str = ""
    depth: int = 0


Pipeline = Callable[[ScrapedItem], ScrapedItem | None]
LinkExtractor = Callable[[BeautifulSoup, str], Iterable[str]]


def default_link_extractor(soup: BeautifulSoup, base_url: str) -> list[str]:
    links: list[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append(urljoin(base_url, href))
    return links


def _normalize_crawl_url(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/") or url


class _DedupSet:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def add(self, url: str) -> bool:
        """Return True if *url* is new (and record it)."""
        key = _normalize_crawl_url(url)
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def __len__(self) -> int:
        return len(self._seen)


class _Throttle:
    """Per-host delay with optional adaptive (autothrottle) adjustment."""

    def __init__(self, delay: float, autothrottle: bool) -> None:
        self._delay = delay
        self._autothrottle = autothrottle
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_delay: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            delay = self._host_delay.get(host, self._delay)
            last = self._last.get(host, 0.0)
            now = time.monotonic()
            sleep_for = max(0.0, delay - (now - last))
            self._last[host] = now + sleep_for
        if sleep_for:
            time.sleep(sleep_for)

    def observe(self, host: str, *, elapsed: float, ok: bool) -> None:
        if not self._autothrottle:
            return
        with self._lock:
            cur = self._host_delay.get(host, self._delay)
            if not ok:
                cur = min(cur * 2 + 0.1, 10.0)
            elif elapsed > 2.0:
                cur = min(cur + 0.1, 10.0)
            elif elapsed < 0.3 and cur > self._delay:
                cur = max(self._delay, cur * 0.9)
            self._host_delay[host] = cur


class CrawlEngine:
    """Synchronous, thread-based concurrent crawler."""

    def __init__(
        self,
        config: CrawlConfig | None = None,
        *,
        fetch_fn: Callable[[str, CrawlConfig], str] | None = None,
        link_extractor: LinkExtractor | None = None,
        pipelines: Sequence[Pipeline] | None = None,
    ) -> None:
        self.config = config or CrawlConfig()
        self._fetch_fn = fetch_fn or _default_fetch
        self._link_extractor = link_extractor or default_link_extractor
        self._pipelines = list(pipelines or [])
        self._deny = [re.compile(p) for p in self.config.deny_patterns]
        self._allow = [re.compile(p) for p in self.config.allow_patterns]
        self._proxy_pool = ProxyPool(self.config.proxy_pool, strategy=self.config.proxy_strategy)

    # --- URL filtering -----------------------------------------------------

    def _url_allowed(self, url: str, seed_domain: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if self.config.same_domain_only and parsed.netloc != seed_domain:
            return False
        if self.config.allowed_domains and parsed.netloc not in self.config.allowed_domains:
            return False
        for pat in self._deny:
            if pat.search(url):
                return False
        if self._allow and not any(pat.search(url) for pat in self._allow):
            return False
        return True

    def _apply_pipelines(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        out: list[ScrapedItem] = []
        for item in items:
            current: ScrapedItem | None = item
            for pipe in self._pipelines:
                if current is None:
                    break
                current = pipe(current)
            if current is not None:
                out.append(current)
        return out

    def _process_one(self, url: str, depth: int, fetch_fn, soup_builder):
        start = time.monotonic()
        host = urlparse(url).netloc
        try:
            html = fetch_fn(url)
            soup = parse(html)
            items = []
            if self.config.schema is not None:
                sch = get_schema(self.config.schema)
                if self.config.system_fields is not None:
                    sch = replace(sch, system_fields=tuple(self.config.system_fields))  # type: ignore[name-defined]
                for rec in sch.extract(soup, base_url=url, strict=self.config.strict_schema):
                    items.append(ScrapedItem(
                        tag=sch.name,
                        text=str(rec.get("title") or rec.get("name") or rec.get("description") or ""),
                        url=str(rec.get("url") or ""),
                        meta=dict(rec),
                    ))
            elif soup_builder is not None:
                items = soup_builder(soup)
            items = self._apply_pipelines(items)
            self._throttle.observe(host, elapsed=time.monotonic() - start, ok=True)
            links = list(self._link_extractor(soup, url))
            if self.config.follow_selector:
                nxt = soup.select_one(self.config.follow_selector)
                if nxt is not None and nxt.get("href"):
                    links.append(urljoin(url, nxt.get("href")))
            return CrawlResult(url=url, status="ok", items=items, depth=depth), links
        except Exception as exc:
            self._throttle.observe(host, elapsed=time.monotonic() - start, ok=False)
            _log.warning("crawl error for %s: %s", url, exc)
            return CrawlResult(url=url, status="error", error=str(exc), depth=depth), []

    def crawl(
        self,
        start_urls: str | Sequence[str],
        *,
        item_builder: Callable[[BeautifulSoup], list[ScrapedItem]] | None = None,
    ) -> list[CrawlResult]:
        """Crawl from *start_urls*, returning one result per fetched URL."""
        if isinstance(start_urls, str):
            start_urls = [start_urls]

        cfg = self.config
        self._throttle = _Throttle(cfg.download_delay, cfg.autothrottle)
        dedup = _DedupSet()
        results: list[CrawlResult] = []

        seed_domain = urlparse(start_urls[0]).netloc
        frontier: list[tuple[str, int]] = []
        for u in start_urls:
            if dedup.add(u):
                frontier.append((u, 0))

        base_fetch = self._fetch_fn

        def fetch_with_retry(url: str) -> str:
            last_exc: Exception | None = None
            for attempt in range(max(1, cfg.retries)):
                self._throttle.wait(urlparse(url).netloc)
                try:
                    return base_fetch(url, cfg)
                except Exception as exc:
                    last_exc = exc
                    if attempt < cfg.retries - 1:
                        time.sleep(0.5 * (2 ** attempt))
            raise SupescrapperError(f"retries exhausted for {url}: {last_exc}")

        while frontier and len(results) < cfg.max_urls:
            batch = frontier[: cfg.concurrency]
            frontier = frontier[cfg.concurrency:]
            with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
                futures = {
                    pool.submit(self._process_one, url, depth, fetch_with_retry, item_builder): url
                    for url, depth in batch
                }
                for fut in as_completed(futures):
                    result, links = fut.result()
                    results.append(result)
                    if cfg.max_depth is not None and result.depth >= cfg.max_depth:
                        continue
                    for link in links:
                        if not self._url_allowed(link, seed_domain):
                            continue
                        if len(results) + len(frontier) >= cfg.max_urls:
                            break
                        if dedup.add(link):
                            frontier.append((link, result.depth + 1))
        return results


class AsyncCrawlEngine:
    """Async concurrent crawler using asyncio tasks."""

    def __init__(
        self,
        config: CrawlConfig | None = None,
        *,
        fetch_fn: Callable[[str, CrawlConfig], "asyncio.Future | object"] | None = None,
        link_extractor: LinkExtractor | None = None,
        pipelines: Sequence[Pipeline] | None = None,
    ) -> None:
        self.config = config or CrawlConfig()
        self._fetch_fn = fetch_fn or _default_async_fetch
        self._link_extractor = link_extractor or default_link_extractor
        self._pipelines = list(pipelines or [])
        self._deny = [re.compile(p) for p in self.config.deny_patterns]
        self._allow = [re.compile(p) for p in self.config.allow_patterns]

    def _url_allowed(self, url: str, seed_domain: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if self.config.same_domain_only and parsed.netloc != seed_domain:
            return False
        if self.config.allowed_domains and parsed.netloc not in self.config.allowed_domains:
            return False
        for pat in self._deny:
            if pat.search(url):
                return False
        if self._allow and not any(pat.search(url) for pat in self._allow):
            return False
        return True

    def _apply_pipelines(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        out: list[ScrapedItem] = []
        for item in items:
            current: ScrapedItem | None = item
            for pipe in self._pipelines:
                if current is None:
                    break
                current = pipe(current)
            if current is not None:
                out.append(current)
        return out

    async def crawl(
        self,
        start_urls: str | Sequence[str],
        *,
        item_builder: Callable[[BeautifulSoup], list[ScrapedItem]] | None = None,
    ) -> list[CrawlResult]:
        if isinstance(start_urls, str):
            start_urls = [start_urls]

        cfg = self.config
        dedup = _DedupSet()
        results: list[CrawlResult] = []
        seed_domain = urlparse(start_urls[0]).netloc

        sem = asyncio.Semaphore(cfg.concurrency)
        host_locks: dict[str, asyncio.Lock] = {}

        async def process(url: str, depth: int) -> tuple[CrawlResult, list[str]]:
            host = urlparse(url).netloc
            host_locks.setdefault(host, asyncio.Lock())
            try:
                async with sem:
                    async with host_locks[host]:
                        if cfg.download_delay:
                            await asyncio.sleep(cfg.download_delay)
                        html = await self._fetch_fn(url, cfg)
                soup = parse(html)
                items = []
                if cfg.schema is not None:
                    sch = get_schema(cfg.schema)
                    if cfg.system_fields is not None:
                        sch = replace(sch, system_fields=tuple(cfg.system_fields))
                    for rec in sch.extract(soup, base_url=url, strict=cfg.strict_schema):
                        items.append(ScrapedItem(
                            tag=sch.name,
                            text=str(rec.get("title") or rec.get("name") or rec.get("description") or ""),
                            url=str(rec.get("url") or ""),
                            meta=dict(rec),
                        ))
                elif item_builder is not None:
                    items = item_builder(soup)
                items = self._apply_pipelines(items)
                links = list(self._link_extractor(soup, url))
                if cfg.follow_selector:
                    nxt = soup.select_one(cfg.follow_selector)
                    if nxt is not None and nxt.get("href"):
                        links.append(urljoin(url, nxt.get("href")))
                return CrawlResult(url=url, status="ok", items=items, depth=depth), links
            except Exception as exc:
                return CrawlResult(url=url, status="error", error=str(exc), depth=depth), []

        async def worker(url: str, depth: int) -> list[tuple[str, int]]:
            result, links = await process(url, depth)
            results.append(result)
            out: list[tuple[str, int]] = []
            if cfg.max_depth is not None and depth >= cfg.max_depth:
                return out
            for link in links:
                if len(results) + len(out) >= cfg.max_urls:
                    break
                if self._url_allowed(link, seed_domain) and dedup.add(link):
                    out.append((link, depth + 1))
            return out

        current: list[tuple[str, int]] = []
        for u in start_urls:
            if dedup.add(u):
                current.append((u, 0))

        while current and len(results) < cfg.max_urls:
            next_items = await asyncio.gather(*(worker(u, d) for u, d in current))
            current = [pair for sub in next_items for pair in sub]
            if len(results) >= cfg.max_urls:
                break
        return results


def _default_fetch(url: str, config: CrawlConfig) -> str:
    from .fetcher import fetch_static
    if config.impersonate:
        from .impersonate import ImpersonatingFetcher, DEFAULT_IMPERSONATE
        target = DEFAULT_IMPERSONATE if config.impersonate is True else config.impersonate
        fetcher = ImpersonatingFetcher(
            impersonate=target, proxies=config.proxy_pool,
            proxy_strategy=config.proxy_strategy, timeout=config.timeout,
            retries=1, headers=_merged_headers(config),
        )
        return fetcher.fetch(url)
    if config.dynamic:
        from .fetcher import fetch_dynamic
        return fetch_dynamic(url, timeout=config.timeout * 1000, user_agent=config.user_agent)
    return fetch_static(
        url, timeout=config.timeout, retries=1,
        user_agent=config.user_agent, headers=_merged_headers(config),
    )


async def _default_async_fetch(url: str, config: CrawlConfig) -> str:
    from .async_scraper import AsyncScraper
    scraper = AsyncScraper()
    await scraper.fetch(
        url, dynamic=config.dynamic, timeout=config.timeout, retries=1,
        user_agent=config.user_agent, headers=_merged_headers(config),
        impersonate=config.impersonate, proxy_pool=config.proxy_pool,
        proxy_strategy=config.proxy_strategy,
    )
    return scraper.html


def _merged_headers(config: CrawlConfig) -> dict[str, str]:
    headers = {"User-Agent": config.user_agent}
    headers.update(config.headers)
    return headers
