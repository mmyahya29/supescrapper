"""Large-scale multi-source news crawler (the ``--news --crawl`` pipeline).

Fetches up to 100k+ article records from many news websites concurrently
(threads), with:

  * seed discovery: RSS feeds (auto-discovered or explicit), Google News RSS
    fallback, and BFS link crawling (``crawl_depth``) for feed-thin sites
  * per-source and global target limits, thread-safe global deduplication
  * polite per-host delay (``crawl_delay``) and retries
  * static (requests / curl_cffi impersonation) fetching with Playwright
    fallback (headless, optionally CAPTCHA-assisted) for defended pages
  * incremental CSV checkpointing + ``--resume`` support
  * final export via the standard exporters (csv/json/xlsx/sqlite)

CLI usage::

    supescrapper --news --crawl \
        --sources https://bbc.com/news https://cnn.com \
        --max-articles 100000 --crawl-concurrency 10 --crawl-depth 2 \
        --export csv -o news_articles.csv
"""
from __future__ import annotations

import atexit
import base64
import csv as _csv
import gzip
import json
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urljoin, urlparse

import requests

from .fetcher import fetch_static
from .parser import is_xml, parse
from .schema import NEWS_SCHEMA, extract_ld_json, get_schema

if TYPE_CHECKING:
    from bs4 import BeautifulSoup
    from rich.console import Console

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

_log = logging.getLogger("supescrapper.news")

# Output column order
CSV_FIELDS = [
    "source_name",
    "website_url",
    "article_url",
    "article_id",
    "title",
    "description",
    "author",
    "time_published",
    "scraped_at",
    "source_urls",
    "method",
]

_SKIP_LINK_PATTERNS = (
    "/tag/", "/topic/", "/author/", "/section/", "/category/", "/search",
    "/video/", "/live/", "/gallery/", "/audio/", "/podcast/", "/interview/",
    # archive/index pages (not articles)
    "/content-index/", "/pages/content-index/", "/archive/", "/index/", "/sitemap/",
    "/year/", "/month/", "/day/", "/date/",
    # image/media asset endpoints (not articles; expensive PW fallbacks)
    "/api/v1/images/", "/resizer/", "/stellar/", "/media-assets/",
)
_STATIC_EXTS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip",
    ".css", ".js", ".mp3",
)
_SEED_CAP_PER_SOURCE = 1000
_SEED_ROUND_WANT = 400
_GN_WINDOWS = ("7d", "14d", "30d")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_of(url: str) -> str:
    return urlparse(url).netloc or url


def _same_site(url: str, base_url: str) -> bool:
    return _host_of(url).replace("www.", "") == _host_of(base_url).replace("www.", "")


def _is_google_news_url(url: str) -> bool:
    return "news.google.com" in (url or "")


def _looks_like_feed(url: str) -> bool:
    """Heuristic: does the URL itself point at an RSS/Atom feed?"""
    if _is_google_news_url(url):
        return True
    path = urlparse(url).path.lower()
    if path.endswith((".xml", ".rss", ".atom")):
        return True
    return path.rstrip("/").endswith(("/rss", "/feed", "/feeds", "/rss.xml"))


# Curated feeds for outlets whose RSS lives on another domain or at a
# non-discoverable path (link rel=alternate + same-domain probes miss these).
KNOWN_FEEDS: dict[str, tuple[str, ...]] = {
    "bbc.com": ("http://feeds.bbci.co.uk/news/rss.xml", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    "cnn.com": ("http://rss.cnn.com/rss/edition.rss",),
    "theguardian.com": ("https://www.theguardian.com/international/rss",),
    "aljazeera.com": ("https://www.aljazeera.com/xml/rss/all.xml",),
    "npr.org": ("https://feeds.npr.org/1001/rss.xml",),
    "techcrunch.com": ("https://techcrunch.com/feed/",),
    "cnbc.com": ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",),
}


def _normalize_url_key(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    key = f"{host}{path}"
    return f"{key}?{parsed.query}" if parsed.query else key


_PUBLIC_SUFFIX_2ND = {"co", "com", "org", "net", "gov", "ac", "edu"}


def _site_core(url: str) -> str:
    """Registrable-domain core of a URL (bbc.com / www.bbc.co.uk -> "bbc").

    Used to keep one source's articles together across its host aliases
    (.com / .co.uk twins, feeds.* subdomains) while rejecting syndicated or
    unrelated hosts that feeds and Google News queries occasionally contain.
    """
    host = _host_of(url).lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if not parts:
        return ""
    if len(parts) >= 3 and parts[-2] in _PUBLIC_SUFFIX_2ND:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def _unique_links(links: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for link in links:
        key = _normalize_url_key(link)
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


# ── thread-safe dedup ───────────────────────────────────────────────────────
class _Dedup:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def add(self, url: str) -> bool:
        key = _normalize_url_key(url)
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def contains(self, url: str) -> bool:
        return _normalize_url_key(url) in self._seen

    def bulk_add(self, keys: Any) -> None:
        with self._lock:
            for key in keys:
                self._seen.add(_normalize_url_key(key))

    def as_list(self) -> list[str]:
        with self._lock:
            return list(self._seen)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


# ── per-host politeness throttle ───────────────────────────────────────────
class _HostThrottle:
    """Schedules each caller its own reservation slot per host (monotone queue)."""

    def __init__(self, delay: float) -> None:
        self._delay = max(0.0, delay)
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def _delay_for(self, host: str) -> float:
        if "news.google.com" in host:
            # Google News is the intermediary fallback feed — keep pacing light
            return min(self._delay, 0.2)
        return self._delay

    def wait(self, url: str) -> None:
        host = _host_of(url)
        delay = self._delay_for(host)
        if delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + delay
        sleep_for = slot - now
        if sleep_for > 0:
            time.sleep(sleep_for)


# ── progress tracking / live display ───────────────────────────────────────
class ProgressTracker:
    """Thread-safe per-source progress records for a news crawl run."""

    def __init__(self, console: Console | None = None, sources: Any = ()) -> None:
        self.console = console
        self.target = 0
        self.source_names: list[str] = []
        for src in sources or ():
            name = src.name if isinstance(src, SourceConfig) else (src or {}).get("name", "")
            if name:
                self.source_names.append(name)
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def init_source(self, name: str) -> None:
        with self._lock:
            self._records.setdefault(
                name, {"count": 0, "status": "init", "detail": "", "errors": 0, "last_update": time.time()}
            )
            if name not in self.source_names:
                self.source_names.append(name)

    def set_status(self, name: str, status: str, detail: str = "") -> None:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                rec = {"count": 0, "status": "init", "detail": "", "errors": 0, "last_update": time.time()}
                self._records[name] = rec
            rec["status"] = status
            rec["detail"] = (detail or "")[:60]
            rec["last_update"] = time.time()

    def add_count(self, name: str, n: int = 1) -> None:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                rec = {"count": 0, "status": "init", "detail": "", "errors": 0, "last_update": time.time()}
                self._records[name] = rec
            rec["count"] += n
            rec["last_update"] = time.time()

    def add_error(self, name: str) -> None:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                rec = {"count": 0, "status": "init", "detail": "", "errors": 0, "last_update": time.time()}
                self._records[name] = rec
            rec["errors"] += 1
            rec["last_update"] = time.time()

    def get_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: rec.copy() for name, rec in self._records.items()}


def _render_progress(tracker: ProgressTracker, elapsed: float) -> None:
    data = tracker.get_all()
    total = sum(rec["count"] for rec in data.values())
    errors = sum(rec.get("errors", 0) for rec in data.values())
    if tracker.console is not None:
        for name in tracker.source_names or sorted(data.keys()):
            rec = data.get(name, {"count": 0, "status": "-", "detail": "", "errors": 0, "last_update": 0.0})
            stalled = " [dim](stalled)[/dim]" if rec["last_update"] and time.time() - rec["last_update"] > 45 else ""
            tracker.console.print(
                f"  [cyan]{name[:26]:<26}[/cyan] {rec['status']:<12} {rec['count']:>4}  {rec['detail']}{stalled}"
            )
        tracker.console.print(
            f"[magenta]news-crawl[/magenta] {total}/{tracker.target} articles, "
            f"errors {errors}, elapsed {elapsed:.0f}s"
        )
    else:
        for name in tracker.source_names or sorted(data.keys()):
            rec = data.get(name, {"count": 0, "status": "-", "detail": "", "errors": 0, "last_update": 0.0})
            _log.info("%-26s %-12s %d %s", name, rec["status"], rec["count"], rec["detail"])


def print_live_progress(tracker: ProgressTracker, stop_event: threading.Event | None = None, interval: float = 1.5) -> None:
    """Background loop that repaints crawl progress; run in a daemon thread."""
    started = time.time()
    while True:
        if stop_event is not None:
            if stop_event.wait(interval):
                break
        else:  # legacy: bare-tracker mode runs until the process exits
            time.sleep(interval)
        _render_progress(tracker, time.time() - started)


# ── configuration ───────────────────────────────────────────────────────────
@dataclass
class SourceConfig:
    """A single news site to crawl."""

    name: str = ""
    base_url: str = ""
    max_articles: int | None = None
    allowed_domains: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceConfig:
        base_url = (data.get("base_url") or data.get("url") or "").strip()
        if base_url and not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url
        parsed = urlparse(base_url)
        name = data.get("name") or parsed.netloc.replace("www.", "") or base_url
        allowed = data.get("allowed_domains") or ([parsed.netloc] if parsed.netloc else [])
        per_source = data.get("max_articles")
        cap = int(per_source) if isinstance(per_source, int) else None
        return cls(name=name, base_url=base_url, max_articles=cap, allowed_domains=list(allowed))


@dataclass
class PlaywrightConfig:
    """Playwright fallback fetch settings."""

    headless: bool = True
    timeout_ms: int = 45_000
    captcha_timeout_sec: int = 300
    scroll_rounds: int = 2
    scroll_delay_ms: int = 900
    wait_until: str = "domcontentloaded"
    viewport: tuple[int, int] = (1920, 1080)
    stealth: bool = True
    persistent_context: bool = False
    context_storage_path: str | None = None


# ── link/export helpers ─────────────────────────────────────────────────────
_ARTICLE_PATTERNS = (
    re.compile(r"/\d{4}/\d{2}/"),
    re.compile(r"/(articles|videos)/[a-z0-9]{10,}"),
    re.compile(r"/\d{6,}"),
)


def _plausible_article(url: str) -> bool:
    path = urlparse(url).path
    # Skip obvious index/archive paths
    if any(pat in path for pat in ("/content-index", "/pages/content-index", "/archive/", "/index/", "/sitemap")):
        return False
    for pat in _ARTICLE_PATTERNS:
        if pat.search(path):
            return True
    # Real articles have descriptive slugs with multiple dashes
    return path.count("-") >= 3 and len(path) > 20


def news_link_extractor(soup: BeautifulSoup, base_url: str, max_links: int = 400) -> list[str]:
    """Extract candidate *article* URLs from a homepage/section page."""
    links: list[str] = []
    seen: set[str] = set()
    selectors = [
        "a[href*='/news/articles/']",      # BBC
        "a[href*='/articles/']",
        "a[href*='/2024/']", "a[href*='/2025/']", "a[href*='/2026/']",
        "article a[href]",
        ".story a[href]", ".post a[href]", ".news-item a[href]", ".headline a[href]",
        "[data-testid*='headline'] a[href]",
        "h2 a[href]", "h3 a[href]",
        "a[href*='/news/'][href*='-']",
        "a[href*='/story/']", "a[href*='/post/']",
    ]
    for selector in selectors:
        for anchor in soup.select(selector):
            href = anchor.get("href") or ""
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            href = urljoin(base_url, href)
            parsed = urlparse(href)
            if parsed.scheme not in ("http", "https"):
                continue
            if not _same_site(href, base_url):
                continue
            if any(pat in href for pat in _SKIP_LINK_PATTERNS):
                continue
            if not _plausible_article(href):
                continue
            key = _normalize_url_key(href)
            if key in seen:
                continue
            seen.add(key)
            links.append(href)
            if len(links) >= max_links:
                return links
    return links


_GOOGLE_HREF_RE = re.compile(r'href="(https?://(?![^"]*\.?google\.)/[^"]+)"')
_URL_LIKE_RE = re.compile(r"https?://[\x21-\x7e]+?https?://|https?://[\x21-\x7e]+")


def _decode_google_news_token(gn_url: str) -> str | None:
    """Best-effort: some Google News tokens still embed the article URL in
    base64-encoded protobuf fields (classic ``CBMi...`` tokens).

    Returns the article URL, or ``None`` when the token is opaque
    (modern ``AU_yqL`` scheme) or garbage.
    """
    path = urlparse(gn_url).path
    token = path.rsplit("/rss/articles/", 1)[-1].rstrip("/") or ""
    if not token:
        return None
    token = token.split("?", 1)[0]
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
    except Exception:
        return None
    text = raw.decode("latin-1", errors="ignore")
    candidates = [c for c in _URL_LIKE_RE.findall(text) if "google.com" not in c]
    if not candidates:
        return None
    # protobuf may append a second (junk) URL; take the cleanest one
    best = min(candidates, key=len)
    return best.strip(" \\\"\x00\x7f") or None


def _resolve_google_news_url(html: str, current_url: str) -> str | None:
    """Google News item pages embed/link the real article URL."""
    try:
        soup = parse(html)
        for anchor in soup.find_all("a", href=True):
            href = urljoin(current_url, anchor["href"])
            parsed = urlparse(href)
            if parsed.scheme in ("http", "https") and "google.com" not in parsed.netloc:
                return href
        match = _GOOGLE_HREF_RE.search(html)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


# ── article extraction ─────────────────────────────────────────────────────
def extract_article_data(soup: BeautifulSoup, url: str, use_ldjson: bool = True) -> list[dict]:
    """Extract article data: ld+json preferred, NEWS_SCHEMA (CSS) fallback.

    Returns ``[{title, description, author, time_published, article_id,
    source_url}]``.
    """
    records: list[dict] = []
    seen_titles: set[str] = set()

    if use_ldjson:
        try:
            ld_items = extract_ld_json(soup)
        except Exception:
            ld_items = []
        for art in ld_items:
            title = (art.get("headline") or "").strip()
            if not title or title.lower() in seen_titles:
                continue
            author = art.get("author")
            if isinstance(author, dict):
                author = author.get("name", "")
            elif isinstance(author, list) and author:
                first = author[0]
                author = first.get("name", "") if isinstance(first, dict) else str(first)
            try:
                page_ref = art.get("mainEntityOfPage")
                url_out = art.get("url") or (page_ref.get("@id", "") if isinstance(page_ref, dict) else "")
            except Exception:
                url_out = ""
            seen_titles.add(title.lower())
            records.append({
                "title": title,
                "description": (art.get("description") or "").strip(),
                "author": (author or "").strip(),
                "time_published": art.get("datePublished", ""),
                "article_id": art.get("@id") or "",
                "source_url": url_out or url,
            })
        if records:
            return records

    try:
        batch = NEWS_SCHEMA.extract(soup, base_url=url)
    except Exception:
        return records
    for rec in batch:
        title = (rec.get("title") or "").strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        records.append({
            "title": title,
            "description": (rec.get("description") or "").strip(),
            "author": (rec.get("author") or "").strip(),
            "time_published": rec.get("published_at", ""),
            "article_id": rec.get("article_id") or rec.get("id") or "",
            "source_url": rec.get("source_urls") or rec.get("url") or url,
        })
    return records


# ── Playwright fallback (thread-local browsers, slot-bounded) ───────────────
class _PlaywrightRuntime:
    """A sync Playwright instance + its browser, owned by one worker thread."""

    def __init__(self, config: PlaywrightConfig) -> None:
        self.config = config
        self.pw: Any = None
        self.browser: Any = None

    def ensure(self) -> bool:
        if self.pw is not None:
            return self.browser is not None
        if not PLAYWRIGHT_AVAILABLE:
            return False
        try:
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(
                headless=self.config.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
        except Exception as exc:
            _log.warning("playwright launch failed: %s", exc)
            self.teardown()
            return False
        return self.browser is not None

    def teardown(self) -> None:
        try:
            if self.browser is not None:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.pw is not None:
                self.pw.stop()
        except Exception:
            pass
        self.browser = None
        self.pw = None


_pw_tlocal = threading.local()
_pw_registry: list[_PlaywrightRuntime] = []
_pw_registry_lock = threading.Lock()
_pw_slots = threading.BoundedSemaphore(4)  # cap concurrent Chromium browsers


def _thread_playwright(config: PlaywrightConfig) -> _PlaywrightRuntime | None:
    runtime = getattr(_pw_tlocal, "runtime", None)
    if runtime is not None and runtime.browser is not None:
        return runtime
    if getattr(_pw_tlocal, "broken", False):
        return None
    runtime = _PlaywrightRuntime(config)
    if not runtime.ensure():
        _pw_tlocal.broken = True
        return None
    _pw_tlocal.runtime = runtime
    with _pw_registry_lock:
        _pw_registry.append(runtime)
    return runtime


def _drop_thread_playwright() -> None:
    _pw_tlocal.runtime = None
    _pw_tlocal.broken = True


def _playwright_cleanup_atexit() -> None:
    for runtime in _pw_registry:
        runtime.teardown()


atexit.register(_playwright_cleanup_atexit)


def _detect_captcha(page: Any) -> bool:
    captcha_selectors = [
        'iframe[src*="recaptcha"]', "div.g-recaptcha", "#recaptcha",
        'iframe[src*="hcaptcha"]', "div.h-captcha",
        "#challenge-form", ".cf-challenge-form",
        'img[alt*="captcha" i]', 'input[name*="captcha"]',
        'div[id*="captcha"]', 'div[class*="captcha"]',
        'text=/verify you are human/i',
    ]
    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            pass
    return False


def _wait_for_captcha(page: Any, source_name: str, url: str, console: Console | None, timeout_sec: int, headless: bool = True) -> None:
    from rich.console import Console

    console = console or Console()

    if headless:
        # In headless: just wait and hope it clears, don't block forever
        console.print(f"[yellow]CAPTCHA detected on {source_name} (headless mode - waiting {timeout_sec}s)[/yellow]")
        start = time.time()
        while time.time() - start < timeout_sec:
            if not _detect_captcha(page):
                console.print("[green]CAPTCHA cleared.[/green]")
                page.wait_for_timeout(1500)
                return
            time.sleep(3)
        console.print("[yellow]CAPTCHA timeout; continuing anyway.[/yellow]")
        return

    # Headed mode: Interactive human solve
    console.print(f"\n[bold yellow]CAPTCHA detected on {source_name}[/bold yellow]")
    console.print(f"[dim]URL: {url}[/dim]")
    console.print("[bold cyan]Solve the CAPTCHA in the browser window...[/bold cyan]")
    start = time.time()
    while time.time() - start < timeout_sec:
        if not _detect_captcha(page):
            console.print("[green]CAPTCHA solved.[/green]")
            page.wait_for_timeout(1500)
            return
        time.sleep(3)
    console.print("[yellow]CAPTCHA auto-detection timeout; continuing.[/yellow]")


def fetch_with_playwright(
    url: str,
    config: PlaywrightConfig | None = None,
    source_name: str = "",
    console: Console | None = None,
) -> str | None:
    """Fetch *url* with a shared per-thread Chromium instance; returns HTML or None.

    CAPTCHA wait engages in both headed and headless modes.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None
    config = config or PlaywrightConfig()
    with _pw_slots:
        runtime = _thread_playwright(config)
        if runtime is None or runtime.browser is None:
            return None
        acquired = getattr(_pw_tlocal, "page_lock", None)
        if acquired is None:
            acquired = threading.Lock()
            _pw_tlocal.page_lock = acquired
        with acquired:
            context = None
            try:
                # Persistent context for cookie/session reuse
                if config.persistent_context:
                    context = getattr(_pw_tlocal, "persistent_context", None)
                    if context is None:
                        context = runtime.browser.new_context(
                            user_agent=_USER_AGENT,
                            viewport={"width": config.viewport[0], "height": config.viewport[1]},
                        )
                        # Load storage state if path provided
                        if config.context_storage_path:
                            storage_path = Path(config.context_storage_path)
                            if storage_path.exists():
                                try:
                                    import json
                                    storage = json.loads(storage_path.read_text(encoding="utf-8"))
                                    if "cookies" in storage:
                                        context.add_cookies(storage["cookies"])
                                    if "origins" in storage:
                                        for origin in storage["origins"]:
                                            context.add_cookies(origin.get("localStorage", []))
                                except Exception as exc:
                                    _log.debug("failed to load storage state: %s", exc)
                        _pw_tlocal.persistent_context = context
                else:
                    context = runtime.browser.new_context(
                        user_agent=_USER_AGENT,
                        viewport={"width": config.viewport[0], "height": config.viewport[1]},
                    )

                page = context.new_page()

                # Apply stealth script to page
                if config.stealth:
                    try:
                        from playwright_stealth import stealth_sync
                        stealth_sync(page)
                    except ImportError:
                        _log.debug("playwright-stealth not installed; skipping stealth")
                    except Exception as exc:
                        _log.debug("stealth_sync failed: %s", exc)

                page.goto(url, timeout=config.timeout_ms, wait_until=config.wait_until)
                if _detect_captcha(page):
                    _wait_for_captcha(page, source_name, url, console, config.captcha_timeout_sec, config.headless)
                for _ in range(config.scroll_rounds):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(config.scroll_delay_ms)
                    if _detect_captcha(page):
                        _wait_for_captcha(page, source_name, url, console, config.captcha_timeout_sec, config.headless)
                html = page.content()

                # Save storage state if persistent context
                if config.persistent_context and config.context_storage_path:
                    try:
                        import json
                        storage = context.storage_state()
                        storage_path = Path(config.context_storage_path)
                        storage_path.parent.mkdir(parents=True, exist_ok=True)
                        storage_path.write_text(json.dumps(storage), encoding="utf-8")
                    except Exception as exc:
                        _log.debug("failed to save storage state: %s", exc)

                if not config.persistent_context:
                    context.close()
                return html
            except Exception as exc:
                _log.debug("playwright fetch failed for %s: %s", url, exc)
                # the renderer may be wedged: rebuild the browser next call
                try:
                    if context is not None and not config.persistent_context:
                        context.close()
                except Exception:
                    pass
                runtime.browser = None
                try:
                    if runtime.pw is not None:
                        runtime.pw.stop()
                except Exception:
                    pass
                runtime.pw = None
                if not runtime.ensure():  # type: ignore[unreachable]
                    _drop_thread_playwright()
                return None


# ── coordinator ────────────────────────────────────────────────────────────
class NewsCrawlEngine:
    """Threaded multi-source news crawler with checkpoint support."""

    def __init__(
        self,
        sources: list[dict | SourceConfig],
        max_articles: int = 100_000,
        max_articles_per_source: int = 1_000,
        max_workers: int = 8,
        article_concurrency: int = 16,
        crawl_depth: int = 2,
        crawl_delay: float = 1.0,
        checkpoint_path: str | None = ".news_checkpoint.json",
        checkpoint_interval: int = 25,
        proxy_pool: list[str] | None = None,
        proxy_strategy: str = "round_robin",
        impersonate: str | bool | None = None,
        timeout: int = 30,
        retries: int = 3,
        export_format: str | None = "csv",
        output_path: str | None = "news_articles.csv",
        console: Console | None = None,
        resume: bool = False,
        playwright_headless: bool = True,
        playwright_fallback: bool = True,
        playwright_stealth: bool = True,
        playwright_persistent: bool = False,
        playwright_context_storage: str | None = None,
        captcha_timeout_sec: int = 300,
        max_seeds_per_source: int = _SEED_CAP_PER_SOURCE,
    ) -> None:
        sources_cfg = [
            src if isinstance(src, SourceConfig) else SourceConfig.from_dict(src)
            for src in (sources or [])
        ]
        self.sources = [src for src in sources_cfg if src.base_url]
        self.max_articles = max(0, int(max_articles))
        self.max_articles_per_source = max(0, int(max_articles_per_source))
        self.max_workers = max(1, int(max_workers))
        self.article_concurrency = max(1, int(article_concurrency))
        self.crawl_depth = max(0, int(crawl_depth))
        self.crawl_delay = max(0.0, float(crawl_delay))
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self.proxy_pool = list(proxy_pool or [])
        self.proxy_strategy = proxy_strategy
        self.impersonate = impersonate
        self.timeout = int(timeout)
        self.retries = max(0, int(retries))
        self.export_format = (export_format or "csv").lower()
        self.output_path = Path(output_path) if output_path else None
        self.console = console
        self.resume = bool(resume)
        self.playwright_fallback = bool(playwright_fallback)
        self.playwright_cfg = PlaywrightConfig(
            headless=playwright_headless,
            stealth=playwright_stealth,
            persistent_context=playwright_persistent,
            context_storage_path=playwright_context_storage,
            captcha_timeout_sec=captcha_timeout_sec,
        )
        self.max_seeds_per_source = max(1, int(max_seeds_per_source))

        # shared crawl state
        self.progress_tracker: ProgressTracker | None = None
        self._stop_event = threading.Event()
        self._progress_stop = threading.Event()
        self._progress_thread: threading.Thread | None = None
        self.global_count = 0
        self.source_counts: dict[str, int] = {}
        self.articles_buffer: list[dict] = []
        self.all_articles: list[dict] = []
        self.buffer_lock = threading.Lock()
        self.global_dedup = _Dedup()
        self._path_keys: set[str] = set()  # "source|path" — cross-host duplicates (bbc.com vs bbc.co.uk)
        self._next_flush = self.checkpoint_interval
        self._throttle = _HostThrottle(self.crawl_delay)
        self._local = threading.local()
        self._gn_meta: dict[str, dict] = {}                 # google-news URL -> item metadata
        self._bfs_visited: dict[str, set[str]] = {}         # source name -> visited crawl URLs
        self._pw_fail: dict[str, int] = {}                  # consecutive PW failures per host
        self._pw_disabled: set[str] = set()                 # hosts with PW circuit-broken open
        self._pw_max_fail = 4

    # ── public ──────────────────────────────────────────────────────────
    def stop(self) -> None:
        """Signal all workers to finish as soon as possible."""
        self._stop_event.set()

    def run(self) -> list[dict]:
        """Run the multi-source crawler and return all collected articles."""
        if not self.sources:
            if self.console:
                self.console.print("[red]No sources provided[/red]")
            return []

        # Setup progress tracker and its live display thread
        self.progress_tracker = ProgressTracker(self.console, self.sources)
        self.progress_tracker.target = self.max_articles
        self._start_progress_thread()

        if self.console:
            self.console.print(
                f"[bold cyan]Starting news crawl: {len(self.sources)} sources, "
                f"target {self.max_articles} articles[/bold cyan]"
            )
            if self.checkpoint_path:
                self.console.print(
                    f"[dim]Checkpoint: {self.checkpoint_path} (interval: {self.checkpoint_interval})[/dim]"
                )
            if self.resume:
                self.console.print("[yellow]Resuming from checkpoint...[/yellow]")

        self._load_checkpoint()

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="src") as pool:
                futures = {pool.submit(self._process_source, src): src.name for src in self.sources}
                for done, fut in enumerate(as_completed(futures), start=1):
                    name = futures[fut]
                    try:
                        fut.result()
                    except KeyboardInterrupt:
                        self._stop_event.set()
                        raise
                    except Exception as exc:
                        if self.console:
                            self.console.print(f"[red]Source '{name}' failed:[/red] {exc}")
                        _log.exception("source crashed: %s", name)
                    if self.console:
                        self.console.print(f"[green]({done}/{len(self.sources)}) '{name}' finished[/green]")
                    if self._stop_event.is_set():
                        break
        except KeyboardInterrupt:
            if self.console:
                self.console.print("\n[yellow]Interrupted — flushing progress...[/yellow]")
        finally:
            self._stop_event.set()
            self._flush_buffer(force=True)
            self._save_checkpoint(force=True)
            self._export_final()
            self._progress_stop.set()

        if self.console:
            self.console.print(f"\n[bold green]Crawl complete: {len(self.all_articles)} articles this run[/bold green]")

        return list(self.all_articles)

    def _start_progress_thread(self) -> None:
        if self._progress_thread is None:
            self._progress_thread = threading.Thread(
                target=print_live_progress,
                args=(self.progress_tracker, self._progress_stop),
                kwargs={"interval": max(4.0, float(self.checkpoint_interval) / 20)},
                daemon=True,
                name="news-progress",
            )
            self._progress_thread.start()

    # ── checkpointing ──────────────────────────────────────────────────
    def _load_checkpoint(self) -> None:
        if not self.resume or self.checkpoint_path is None:
            return
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except Exception as exc:
            if self.console:
                self.console.print(f"[yellow]Could not read checkpoint: {exc}[/yellow]")
            return
        seen = data.get("seen") or []
        self.global_dedup.bulk_add(str(key) for key in seen)
        for pk in data.get("path_keys") or []:
            self._path_keys.add(str(pk))
        counts = data.get("source_counts") or {}
        if isinstance(counts, dict):
            self.source_counts = {str(k): int(v) for k, v in counts.items() if isinstance(v, int)}
        self.global_count = sum(self.source_counts.values()) if self.source_counts else int(data.get("total") or 0)
        self._next_flush = (self.global_count // self.checkpoint_interval) * self.checkpoint_interval + self.checkpoint_interval
        if self.console:
            self.console.print(
                f"[dim]Restored {len(seen)} seen URLs, {self.global_count} article counts from checkpoint[/dim]"
            )
        # Sync progress tracker with restored counts
        if self.progress_tracker:
            for name, count in self.source_counts.items():
                self.progress_tracker.add_count(name, count)

    def _save_checkpoint(self, force: bool = False) -> None:
        if self.checkpoint_path is None:
            return
        if not force and self.global_count == 0 and not self.source_counts:
            return
        try:
            with self.buffer_lock:
                path_keys = sorted(self._path_keys)
            self.checkpoint_path.write_text(
                json.dumps({
                    "version": 1,
                    "saved_at": _now_utc(),
                    "total": self.global_count,
                    "source_counts": dict(self.source_counts),
                    "seen": self.global_dedup.as_list(),
                    "path_keys": path_keys,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            _log.warning("checkpoint write failed: %s", exc)

    def _flush_buffer(self, force: bool = False) -> None:
        """Persist buffered articles to CSV, then save the checkpoint."""
        with self.buffer_lock:
            pending = self.articles_buffer[:]
            self.articles_buffer.clear()
        if pending or force:
            self._append_csv(pending)
            if self.console and pending:
                self.console.print(f"[dim]💾 Flushed {len(pending)} articles to {self.output_path} (total: {self.global_count})[/dim]")
        if pending or force:
            self._save_checkpoint(force=True)

    def _append_csv(self, rows: list[dict]) -> None:
        if self.export_format not in ("csv", "") or not self.output_path or not rows:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.output_path.exists()
        with self.output_path.open("a", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(rows)

    def _export_final(self) -> None:
        if not self.output_path or not self.all_articles:
            return
        fmt = self.export_format or "csv"
        if fmt == "csv" and self.resume:
            return  # CSV appends incrementally; a rewrite would drop past runs
        try:
            from .exporters import export_records

            dest = self.output_path
            if fmt == "xls":
                dest = dest.with_suffix(".xlsx")
                fmt = "xlsx"
            path = export_records(self.all_articles, fmt, str(dest))
            if self.console:
                self.console.print(f"[green]Exported {fmt}:[/green] {path}")
        except Exception as exc:
            if self.console:
                self.console.print(f"[red]Export failed:[/red] {exc}")

    # ── bookkeeping ────────────────────────────────────────────────────
    def _target_reached(self) -> bool:
        return self.global_count >= self.max_articles

    def _cap_for(self, source: SourceConfig) -> int:
        return source.max_articles if source.max_articles is not None else self.max_articles_per_source

    def _family_cores(self, source: SourceConfig) -> set[str]:
        """Registrable-domain cores identifying a source's own host family."""
        cores = {_site_core(d) for d in source.allowed_domains or []}
        cores.add(_site_core(source.base_url))
        cores.discard("")
        return cores

    def _family_urls(self, records: list[dict], fetched_url: str, family: set[str]) -> list[dict]:
        """Force record URLs onto the fetched page's host.

        Syndicated articles keep the original outlet's canonical URL in their
        ld+json (``url`` / ``mainEntityOfPage``); recording it would attribute
        e.g. a Hollywood Reporter piece hosted on bbc.com to hollywoodreporter.
        """
        for data in records:
            url_out = (data.get("source_url") or "").strip()
            if not url_out or (_site_core(url_out) not in family and not _is_google_news_url(url_out)):
                data["source_url"] = fetched_url
        return records

    def _record_article(self, source: SourceConfig, article_data: dict, method: str) -> bool:
        """Add a deduped article record under lock; returns False if discarded."""
        title = (article_data.get("title") or "").strip()
        if not title:
            return False
        article_url = article_data.get("source_url") or article_data.get("article_url") or ""
        key = article_url or title[:120]
        with self.buffer_lock:
            if self._target_reached():
                return False
            if self.source_counts.get(source.name, 0) >= self._cap_for(source):
                return False
            if not self.global_dedup.add(key):
                return False
            # same path served under different hosts (www. aliases, .com/.co.uk
            # twins) is the same article — drop it for uniqueness
            path_key = f"{source.name}|{urlparse(article_url).path}" if article_url else ""
            if path_key and path_key in self._path_keys:
                return False
            if path_key:
                self._path_keys.add(path_key)
            record = {
                "source_name": source.name,
                "website_url": source.base_url,
                "article_url": article_url,
                "article_id": article_data.get("article_id", ""),
                "title": title,
                "description": article_data.get("description", ""),
                "author": article_data.get("author", ""),
                "time_published": article_data.get("time_published", ""),
                "scraped_at": _now_utc(),
                "source_urls": article_url,
                "method": method,
            }
            self.articles_buffer.append(record)
            self.all_articles.append(record)
            self.global_count += 1
            self.source_counts[source.name] = self.source_counts.get(source.name, 0) + 1
            target_hit = self._target_reached()
        if self.progress_tracker is not None:
            self.progress_tracker.add_count(source.name)
        if self.global_count >= self._next_flush:
            self._next_flush += self.checkpoint_interval
            self._flush_buffer()
        if target_hit:
            self._stop_event.set()
        return True

    # ── fetching ───────────────────────────────────────────────────────
    def _static_fetch(self, url: str) -> str:
        self._throttle.wait(url)
        if self.impersonate:
            fetcher = getattr(self._local, "imp_fetcher", None)
            if fetcher is None:
                from .impersonate import DEFAULT_IMPERSONATE, ImpersonatingFetcher

                target = DEFAULT_IMPERSONATE if self.impersonate is True else str(self.impersonate)
                fetcher = ImpersonatingFetcher(
                    impersonate=target,
                    proxies=self.proxy_pool or None,
                    proxy_strategy=self.proxy_strategy,
                    timeout=self.timeout,
                    retries=self.retries,
                )
                self._local.imp_fetcher = fetcher
            return fetcher.fetch(url)
        return fetch_static(url, timeout=self.timeout, retries=self.retries, proxies=None)

    # ── seed discovery ─────────────────────────────────────────────────
    def _discover_rss_feeds(self, base_url: str) -> list[str]:
        if _looks_like_feed(base_url):
            return [base_url]
        feeds: list[str] = []
        # curated cross-domain feeds first (discovery misses these)
        host = _host_of(base_url).replace("www.", "").lower()
        feeds.extend(KNOWN_FEEDS.get(host, ()))
        try:
            soup = parse(self._static_fetch(base_url))
            for link in soup.find_all("link", rel="alternate"):
                type_attr = (link.get("type") or "").lower()
                href = link.get("href") or ""
                if href and ("rss" in type_attr or "atom" in type_attr or "xml" in type_attr):
                    if not href.startswith(("http://", "https://")):
                        href = urljoin(base_url, href)
                    feeds.append(href)
        except Exception as exc:
            _log.info("rss discovery failed for %s: %s", base_url, exc)
        if len(feeds) < 3:
            for path in ("/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml", "/rss/"):
                if len(feeds) >= 6:
                    break
                probe = urljoin(base_url, path)
                if any(probe.rstrip("/") == f.rstrip("/") for f in feeds):
                    continue
                try:
                    self._throttle.wait(probe)
                    probe_html = fetch_static(probe, timeout=min(self.timeout, 6), retries=1)
                    if is_xml(probe_html):
                        feeds.append(probe)
                except Exception:
                    continue
        return _unique_links(feeds)

    def _google_news_urls(self, base_url: str) -> list[str]:
        domain = _host_of(base_url).replace("www.", "")
        if not domain:
            return []
        out: list[str] = []
        for window in _GN_WINDOWS:
            query = urlencode({"q": f"site:{domain} when:{window}", "hl": "en-US", "gl": "US", "ceid": "US:en"})
            out.append(f"https://news.google.com/rss/search?{query}")
        return out

    def _urls_from_feed(self, feed_url: str) -> tuple[list[str], dict[str, dict]]:
        """Fetch a feed; returns (article URLs, google-news metadata per URL).

        Google News items keep their real article URL hidden behind an opaque
        redirect token. Their RSS entries still carry title/description/pubDate,
        which we preserve in *meta* for a direct record when resolution fails.
        """
        try:
            html = self._static_fetch(feed_url)
        except Exception:
            return [], {}
        if not html or not is_xml(html):
            return [], {}
        try:
            batch = get_schema("rss").extract(parse(html), base_url=feed_url)
        except Exception:
            return [], {}
        urls: list[str] = []
        meta: dict[str, dict] = {}
        gn_feed = _is_google_news_url(feed_url)
        for rec in batch:
            url = rec.get("source_urls") or rec.get("url") or rec.get("link") or ""
            if not url.startswith("http"):
                continue
            if any(pat in url for pat in _SKIP_LINK_PATTERNS):
                continue
            urls.append(url)
            if gn_feed:
                description = re.sub(r"<[^>]+>", " ", rec.get("description") or "").strip()
                if description.lower().startswith(("read more", "view full text", "read the full")):
                    description = ""
                meta[url] = {
                    "title": rec.get("title") or "",
                    "description": description,
                    "published": rec.get("published_at") or rec.get("article_id", ""),
                }
        return urls, meta

    def _gn_meta_record(self, gn_url: str, meta: dict) -> dict | None:
        """Direct record straight off Google News feed metadata (no extraction)."""
        title = (meta.get("title") or "").strip()
        if not title:
            return None
        # GN titles conventionally end with " - Site Name"; trim a short tail
        head, sep, tail = title.rpartition(" - ")
        if sep and tail and len(tail) <= 30 and len(head) > 20:
            title = head
        description = (meta.get("description") or "").strip()
        if len(description) < 12:
            description = ""
        return {
            "title": title,
            "description": description,
            "author": "",
            "time_published": meta.get("published", ""),
            "article_id": "",
            "source_url": gn_url,
        }

    def _sitemap_seeds(self, source: SourceConfig, tracker: ProgressTracker, seeds: list[str], used: set[str], budget: int) -> None:
        """Harvest article URLs from sitemap.xml / news-sitemap.xml indexes.

        Sitemaps are the machine-readable URL index most outlets publish and
        can scale to thousands of pages per source.
        """
        host = _host_of(source.base_url).replace("www.", "")
        root = f"https://{host}"
        origin = source.base_url.rstrip("/")
        candidates: list[str] = []

        # 1) robots.txt Sitemap: directives (the site's own declaration)
        try:
            self._throttle.wait(f"{root}/robots.txt")
            robots = self._static_fetch(f"{root}/robots.txt")
            for line in (robots or "").splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_url = line.split(":", 1)[1].strip()
                    if sitemap_url.startswith("http"):
                        candidates.append(sitemap_url)
        except Exception:
            pass

        # 2) conventional paths
        candidates += [
            f"{root}/sitemap.xml",
            f"{origin}/sitemap.xml",
            f"{root}/news-sitemap.xml",
            f"{root}/sitemap-news.xml",
            f"{root}/sitemap_index.xml",
            f"{root}/news/sitemap.xml",
        ]
        seen_candidates: set[str] = set()
        for candidate in candidates:
            if self._stop_event.is_set() or len(seeds) >= budget or candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            tracker.set_status(source.name, "sitemaps", f"reading {candidate[:48]}")
            before = len(seeds)
            self._read_sitemap(candidate, seeds, used, budget, depth=0)
            if len(seeds) > before:
                break  # first productive sitemap is usually the right one

    def _fetch_gz_sitemap(self, url: str) -> str | None:
        """Fetch and decompress a gzipped sitemap (.gz)."""
        try:
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return gzip.decompress(resp.content).decode("utf-8", errors="replace")
        except Exception as exc:
            _log.debug("gz sitemap fetch failed %s: %s", url, exc)
            return None

    def _read_sitemap(self, url: str, seeds: list[str], used: set[str], budget: int, depth: int) -> None:
        if depth > 3 or len(seeds) >= budget or self._stop_event.is_set():
            return
        try:
            self._throttle.wait(url)
            if url.endswith(".gz"):
                html = self._fetch_gz_sitemap(url)
            else:
                html = self._static_fetch(url)
        except Exception:
            return
        if not html or not is_xml(html):
            return
        try:
            soup = parse(html)
        except Exception:
            return
        locs = _unique_links([
            (tag.get_text() or "").strip()
            for tag in soup.find_all("loc")
            if (tag.get_text() or "").strip()
        ])
        nested: list[str] = []
        for loc in locs:
            path = urlparse(loc).path.lower()
            # index-of-sitemaps regardless of suffix (Reuters ends /?outputType=xml)
            if "sitemap" in path or "outboundfeeds" in loc.lower() or loc.endswith((".xml", ".gz")):
                if len(nested) < 12 and depth < 3:
                    nested.append(loc)
                continue
            key = _normalize_url_key(loc)
            if key in used or self.global_dedup.contains(loc):
                continue
            if any(pat in loc for pat in _SKIP_LINK_PATTERNS):
                continue
            if len(seeds) >= budget:
                break
            seeds.append(loc)
        # news/article/edition sitemaps beating generic aggregates keeps
        # harvested URLs on-topic (legacy test/archive pages live elsewhere)
        nested.sort(key=lambda u: ("news" not in u and "article" not in u, u))
        for nest in nested:
            if len(seeds) >= budget or self._stop_event.is_set():
                break
            self._read_sitemap(nest, seeds, used, budget, depth + 1)

    def _bfs_seeds(self, source: SourceConfig, tracker: ProgressTracker, seeds: list[str]) -> None:
        """Crawl section pages with BFS and harvest plausible article links."""
        if self.crawl_depth <= 0:
            return
        allowed = list(source.allowed_domains or [])
        visited = self._bfs_visited.setdefault(source.name, set())
        seed_keys: set[str] = {_normalize_url_key(s) for s in seeds}
        frontier: list[tuple[str, int]] = [(source.base_url, 0)]
        pages_visited = 0
        max_pages = max(6, self.crawl_depth * 4)
        while frontier and pages_visited < max_pages:
            if self._stop_event.is_set():
                break
            url, depth = frontier.pop(0)
            key = _normalize_url_key(url)
            if key in visited:
                continue
            visited.add(key)
            pages_visited += 1
            tracker.set_status(source.name, "crawling", f"depth {depth}: {url[:48]}")
            try:
                html = self._static_fetch(url)
            except Exception:
                continue
            if not html:
                continue
            soup = parse(html)
            for link in news_link_extractor(soup, url):
                link_key = _normalize_url_key(link)
                if link_key not in seed_keys:
                    seed_keys.add(link_key)
                    seeds.append(link)
            frontier_links: list[str] = []
            for anchor in soup.select("a[href]"):
                href = anchor.get("href") or ""
                if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                    continue
                href = urljoin(url, href)
                parsed = urlparse(href)
                if parsed.scheme not in ("http", "https") or _is_google_news_url(href):
                    continue
                if allowed:
                    if not any(parsed.netloc == d or parsed.netloc.endswith("." + d) for d in allowed):
                        continue
                elif not _same_site(href, source.base_url):
                    continue
                path = parsed.path
                if not path or path.endswith(_STATIC_EXTS) or _looks_like_feed(href):
                    continue
                if any(pat in href for pat in _SKIP_LINK_PATTERNS):
                    continue
                frontier_links.append(href)
            if depth + 1 <= self.crawl_depth:
                for link in _unique_links(frontier_links):
                    if _normalize_url_key(link) in visited:
                        continue
                    frontier.append((link, depth + 1))

    def _collect_seed_urls(self, source: SourceConfig, tracker: ProgressTracker, used: set[str]) -> list[str]:
        """RSS → sitemap harvest → BFS crawl → Google News last-resort.

        Real article URLs are preferred (full metadata); Google News feed
        metadata records are the fallback when a site blocks everything else.
        """
        seeds: list[str] = []
        remaining = max(10, min(self.max_seeds_per_source, self._cap_for(source) - self.source_counts.get(source.name, 0)))
        # scale the per-round target with what the source still needs, so the
        # sitemap/BFS stages engage instead of stopping at RSS's ~50 items
        want = min(remaining, _SEED_ROUND_WANT)

        # Stage 1: RSS (discovered feeds, or the URL itself when it is a feed)
        tracker.set_status(source.name, "rss", "discovering feeds")
        for feed in self._discover_rss_feeds(source.base_url):
            if len(seeds) >= want or self._stop_event.is_set():
                break
            urls, _meta = self._urls_from_feed(feed)
            for url in urls:
                if self.global_dedup.contains(url):
                    continue
                if _normalize_url_key(url) in used:
                    continue
                seeds.append(url)
                if len(seeds) >= want * 2:
                    break
        tracker.set_status(source.name, "seeding", f"{len(seeds)} URLs from RSS")

        # Stage 2: sitemap harvest — the scalable URL index for big caps
        if len(seeds) < want:
            self._sitemap_seeds(source, tracker, seeds, used, budget=min(self.max_seeds_per_source, remaining * 8))

        # Stage 3: BFS crawl for whatever sitemaps missed
        if len(seeds) < want:
            self._bfs_seeds(source, tracker, seeds)

        # Stage 4: Google News fallback (items double as meta-only records)
        if len(seeds) < want:
            tracker.set_status(source.name, "gn-fallback", "querying Google News")
            for feed in self._google_news_urls(source.base_url):
                if len(seeds) >= want or self._stop_event.is_set():
                    break
                urls, meta = self._urls_from_feed(feed)
                if not urls:
                    break  # an empty window means later windows won't help either
                self._gn_meta.update(meta)
                for url in urls:
                    if self.global_dedup.contains(url) or _normalize_url_key(url) in used:
                        continue
                    seeds.append(url)

        out: list[str] = []
        seen_keys: set[str] = set()
        family = self._family_cores(source)
        for seed in seeds:
            key = _normalize_url_key(seed)
            if key in seen_keys or key in used or self.global_dedup.contains(seed):
                continue
            # feeds / Google News results occasionally carry other outlets'
            # articles; only this source's host family (bbc.com == bbc.co.uk)
            # passes. Google News redirect URLs are exempt — they resolve to
            # the real (family-checked) article URL at fetch time.
            if not _is_google_news_url(seed) and _site_core(seed) not in family:
                continue
            seen_keys.add(key)
            out.append(seed)
        tracker.set_status(source.name, "seeding", f"{len(out)} seed URLs")
        return out

    # ── per-source pipeline ────────────────────────────────────────────
    def _process_source(self, source: SourceConfig) -> int:
        tracker = self.progress_tracker or ProgressTracker(None, [source])
        try:
            tracker.init_source(source.name)
            current = self.source_counts.get(source.name, 0)
            if self._target_reached() or current >= self._cap_for(source):
                tracker.set_status(source.name, "done", f"skipped ({current} already)")
                return current

            if self.console:
                self.console.print(f"[cyan]Source {source.name}: crawling {source.base_url}[/cyan]")

            used_seeds: set[str] = set()
            article_workers = self.article_concurrency
            total_fetched = 0
            seed_budget = max(12, self.max_seeds_per_source)

            # rounds: keep refilling the seed pool as long as per-source cap
            # allows; scale the count so large caps (10k+/source) are reachable
            cap = self._cap_for(source)
            max_rounds = max(8, cap // 200 + 1)
            for _round in range(max_rounds):
                if self._target_reached() or self._stop_event.is_set():
                    break
                if self.source_counts.get(source.name, 0) >= self._cap_for(source):
                    break
                seeds = self._collect_seed_urls(source, tracker, used_seeds)
                if not seeds:
                    break
                # cap per-round submissions near what the source still needs
                needed = self._cap_for(source) - self.source_counts.get(source.name, 0)
                seeds = seeds[: max(24, min(len(seeds), needed * 4, seed_budget))]
                for seed in seeds:
                    used_seeds.add(_normalize_url_key(seed))
                tracker.set_status(source.name, "fetching", f"fetching {len(seeds)} articles")

                fetched_any = self._fetch_seed_round(source, tracker, seeds, article_workers)
                if fetched_any == 0:
                    break  # this round produced nothing; next round unlikely better
                total_fetched += fetched_any
            return total_fetched
        except KeyboardInterrupt:
            self._stop_event.set()
            raise
        except Exception as exc:
            tracker.add_error(source.name)
            if self.console:
                self.console.print(f"[red]Error crawling {source.name}: {exc}[/red]")
            _log.exception("source error %s", source.name)
            return 0
        finally:
            tracker.set_status(
                source.name,
                "done",
                f"{self.source_counts.get(source.name, 0)} articles",
            )

    def _fetch_seed_round(self, source: SourceConfig, tracker: ProgressTracker, seeds: list[str], article_workers: int) -> int:
        """Fetch one batch of seeds concurrently, recording what worked."""
        fetched_any = 0
        family = self._family_cores(source)

        def fetch_and_extract(art_url: str) -> tuple[str, list[dict]]:
            if _is_google_news_url(art_url):
                # Google News items hide the real URL behind an opaque token;
                # decode when possible, else record straight from feed metadata.
                meta = self._gn_meta.get(art_url, {})
                real_url = _decode_google_news_token(art_url) or self._resolve_gn(art_url)
                if real_url and not _is_google_news_url(real_url):
                    if _site_core(real_url) not in family:
                        return "", []  # off-site noise from the site: query
                    method, records = self._fetch_article(real_url, source.name)
                    if records:
                        return method, self._family_urls(records, real_url, family)
                rec = self._gn_meta_record(art_url, meta)
                if rec:
                    return "gn-index", [rec]
                return "", []
            method, records = self._fetch_article(art_url, source.name)
            if records:
                return method, self._family_urls(records, art_url, family)
            return method, records

        with ThreadPoolExecutor(max_workers=article_workers, thread_name_prefix="art") as pool:
            futures: list[Future] = [pool.submit(fetch_and_extract, url) for url in seeds]
            for fut in as_completed(futures):
                if self._stop_event.is_set():
                    break
                try:
                    method, records = fut.result()
                except Exception as exc:
                    tracker.add_error(source.name)
                    _log.debug("article task failed: %s", exc)
                    continue
                if not method:
                    continue
                for data in records:
                    if self._record_article(source, data, method):
                        fetched_any += 1
                    if self._target_reached():
                        break
        return fetched_any

    def _fetch_article(self, art_url: str, source_name: str) -> tuple[str, list[dict]]:
        """Hybrid fetch: impersonation -> static -> playwright (with stealth)."""
        # 1. Try impersonation first (fast, TLS fingerprint)
        if self.impersonate:
            try:
                html = self._static_fetch(art_url)
                if html:
                    records = extract_article_data(parse(html), art_url, use_ldjson=True)
                    if records:
                        return "impersonate", records
            except Exception as exc:
                if "404" in str(exc) or "410" in str(exc):
                    return "", []
                _log.debug("impersonation fetch failed for %s: %s", art_url, exc)

        # 2. Fallback: Try static requests (no impersonation)
        try:
            html = fetch_static(art_url, timeout=self.timeout, retries=self.retries, proxies=None)
            if html:
                records = extract_article_data(parse(html), art_url, use_ldjson=True)
                if records:
                    return "static", records
        except Exception as exc:
            if "404" in str(exc) or "410" in str(exc):
                return "", []

        # 3. Last resort: Playwright with stealth
        if not self.playwright_fallback or not PLAYWRIGHT_AVAILABLE:
            return "", []
        host = _host_of(art_url)
        if host in self._pw_disabled:
            # circuit open: PW fails consistently on this host — don't waste slots
            return "", []
        pw_html = fetch_with_playwright(art_url, self.playwright_cfg, source_name, self.console)
        if pw_html:
            records = extract_article_data(parse(pw_html), art_url, use_ldjson=True)
            if records:
                self._pw_fail[host] = 0
                return "playwright", records
        failures = self._pw_fail.get(host, 0) + 1
        self._pw_fail[host] = failures
        if failures >= self._pw_max_fail:
            self._pw_disabled.add(host)
            _log.info("playwright disabled for %s after %d failures", host, failures)
        return "", []

    def _resolve_gn(self, gn_url: str) -> str:
        """Resolve a Google News item URL to the real article URL (best effort)."""
        try:
            html = self._static_fetch(gn_url)
            if html:
                real = _resolve_google_news_url(html, gn_url)
                if real:
                    return real
        except Exception:
            pass
        return gn_url


def crawl_news_sources(
    sources: list[dict | SourceConfig],
    max_articles: int = 100_000,
    max_articles_per_source: int = 1_000,
    max_workers: int = 8,
    article_concurrency: int = 16,
    crawl_depth: int = 2,
    crawl_delay: float = 1.0,
    checkpoint_path: str | None = ".news_checkpoint.json",
    checkpoint_interval: int = 25,
    proxy_pool: list[str] | None = None,
    proxy_strategy: str = "round_robin",
    impersonate: str | bool | None = None,
    timeout: int = 30,
    retries: int = 3,
    export_format: str | None = "csv",
    output_path: str | None = "news_articles.csv",
    console: Console | None = None,
    resume: bool | None = None,
    playwright_headless: bool = True,
    playwright_fallback: bool = True,
    playwright_stealth: bool = True,
    playwright_persistent: bool = False,
    playwright_context_storage: str | None = None,
    captcha_timeout_sec: int = 300,
) -> list[dict]:
    """Crawl multiple news sources concurrently and return collected records.

    When ``resume`` is ``None`` and *checkpoint_path* exists, the crawl
    automatically resumes from that checkpoint (crash-safe reruns).
    """
    if resume is None:
        resume = bool(checkpoint_path and Path(checkpoint_path).exists())
    engine = NewsCrawlEngine(
        sources=sources,
        max_articles=max_articles,
        max_articles_per_source=max_articles_per_source,
        max_workers=max_workers,
        article_concurrency=article_concurrency,
        crawl_depth=crawl_depth,
        crawl_delay=crawl_delay,
        checkpoint_path=checkpoint_path,
        checkpoint_interval=checkpoint_interval,
        proxy_pool=proxy_pool,
        proxy_strategy=proxy_strategy,
        impersonate=impersonate,
        timeout=timeout,
        retries=retries,
        export_format=export_format,
        output_path=output_path,
        console=console,
        resume=resume,
        playwright_headless=playwright_headless,
        playwright_fallback=playwright_fallback,
        playwright_stealth=playwright_stealth,
        playwright_persistent=playwright_persistent,
        playwright_context_storage=playwright_context_storage,
        captcha_timeout_sec=captcha_timeout_sec,
    )
    return engine.run()


__all__ = [
    "CSV_FIELDS",
    "PLAYWRIGHT_AVAILABLE",
    "NewsCrawlEngine",
    "PlaywrightConfig",
    "ProgressTracker",
    "SourceConfig",
    "crawl_news_sources",
    "extract_article_data",
    "fetch_with_playwright",
    "news_link_extractor",
    "print_live_progress",
]
