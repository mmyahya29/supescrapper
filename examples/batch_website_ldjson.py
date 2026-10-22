#!/usr/bin/env python3
"""
Batch website scraper with fallback (DEMONSTRATION):

1. Try normal HTTP fetch on main website URL (homepage + section pages)
2. If fails (403, 404, empty), use Playwright headed browser
3. Extract article links, then fetch each article page
4. Extract data using ld+json (preferred) or CSS fallback (NEWS_SCHEMA)

NOTE: This is a DEMONSTRATION. Website scraping for news has severe limitations:
- Most news sites block automated access to article pages (403, paywalls, JS challenges)
- Article URLs from homepages often point to section pages, not articles
- ld+json is only present on ~30% of article pages (CNN yes, BBC no)
- For reliable high-volume news collection, use batch_rss_collect.py instead

This example shows the TECHNIQUE: static → Playwright fallback, ld+json → CSS fallback.
"""

from __future__ import annotations

import csv
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from rich.console import Console

from supescrapper.fetcher import fetch_static
from supescrapper.parser import parse
from supescrapper.schema import NEWS_SCHEMA, extract_ld_json

# Try to import Playwright
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ┌──────────────────────────────────────────────────────────────────────────┐
# │ CONFIGURATION                                                            │
# └──────────────────────────────────────────────────────────────────────────┘
SOURCES_CSV = "sources.csv"
OUTPUT_CSV = "news_output_websites.csv"
TARGET_PER_SOURCE = 10          # Demo target (not 100 - most sites block at scale)
WORKERS = 3                     # Concurrent sources
ARTICLE_WORKERS = 5             # Concurrent article pages per source
MAX_LINKS_PER_SECTION = 15      # Max article links to try per section
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 2
BROWSER_TIMEOUT = 60000         # ms
STATUS_INTERVAL = 1.0
HEADLESS = False                # Headed browser for fallback
# ┌──────────────────────────────────────────────────────────────────────────┘

FIELDNAMES = [
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
    "method",  # "static-ldjson", "static-css", "playwright-ldjson", "playwright-css"
]


class ProgressTracker:
    _lock = threading.Lock()
    _records: dict[str, dict[str, Any]] | None = None

    @classmethod
    def _ensure(cls) -> None:
        if cls._records is None:
            cls._records = {}

    @classmethod
    def init(cls, name: str) -> None:
        cls._ensure()
        with cls._lock:
            cls._records[name] = {"count": 0, "status": "init", "detail": "", "last_update": time.time()}

    @classmethod
    def add(cls, name: str, n: int = 1) -> None:
        cls._ensure()
        with cls._lock:
            if name in cls._records:
                cls._records[name]["count"] += n
                cls._records[name]["last_update"] = time.time()

    @classmethod
    def set_status(cls, name: str, status: str, detail: str = "") -> None:
        cls._ensure()
        with cls._lock:
            if name in cls._records:
                cls._records[name]["status"] = status
                cls._records[name]["detail"] = detail[:80]
                cls._records[name]["last_update"] = time.time()

    @classmethod
    def get_all(cls) -> dict[str, dict[str, Any]]:
        cls._ensure()
        with cls._lock:
            return {k: v.copy() for k, v in cls._records.items()}


def status_printer() -> None:
    while True:
        time.sleep(STATUS_INTERVAL)
        data = ProgressTracker.get_all()
        if not data:
            continue
        print("\033[H\033[2J", end="")
        print(f"=== Website Batch Scraper (Demo)  ({time.strftime('%H:%M:%S')}) ===")
        print(f"{'Source':<28} {'Status':<14} {'Count/10':>10}  Detail")
        print("-" * 90)
        for name in sorted(data.keys()):
            d = data[name]
            stuck = " <-- STUCK?" if (time.time() - d["last_update"]) > 30 else ""
            print(f"{name[:27]:<28} {d['status']:<14} {d['count']:>2}/10  {d['detail']}{stuck}")
        print("-" * 90)
        done = sum(1 for d in data.values() if d["status"] == "done")
        total = sum(d["count"] for d in data.values())
        print(f"Done: {done}/{len(data)}  |  Total: {total}")


def read_sources(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ensure_output(path: str) -> None:
    p = Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


def append_rows(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with Path(path).open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerows(rows)


def extract_article_links(soup, base_url: str, max_links: int = 200) -> list[str]:
    """Extract article URLs from a homepage/section page."""
    links = []
    seen = set()

    selectors = [
        "a[href*='/news/articles/']",  # BBC
        "a[href*='/news/videos/']",    # BBC videos
        "a[href*='/2024/']", "a[href*='/2025/']", "a[href*='/2026/']",  # date in URL
        "a[href*='/article/']",
        "a[href*='/news/'][href*='-']",
        "a[href*='/story/']",
        "a[href*='/post/']",
        "article a[href]",
        ".story a[href]",
        ".post a[href]",
        ".news-item a[href]",
        ".headline a[href]",
        "[data-testid*='headline'] a[href]",
        "h2 a[href]", "h3 a[href]",
    ]

    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href", "")
            if not href:
                continue
            if not href.startswith(("http://", "https://")):
                href = urljoin(base_url, href)
            parsed = urlparse(href)
            base_parsed = urlparse(base_url)
            if parsed.netloc != base_parsed.netloc:
                continue
            if any(skip in href for skip in ["/tag/", "/topic/", "/author/", "/section/", "/category/", "/search", "/video/", "/live/", "/gallery/", "/audio/", "/podcast/"]):
                continue
            path = parsed.path
            if not (re.search(r"/\d{4}/\d{2}/\d{2}/", path) or
                    re.search(r"/\d{4}/\d{2}/", path) or
                    re.search(r"/(articles|videos)/[a-z0-9]{10,}", path) or
                    re.search(r"/\d{6,}", path) or
                    ("-" in path and len(path) > 20)):
                continue
            if href in seen:
                continue
            seen.add(href)
            links.append(href)
            if len(links) >= max_links:
                return links
    return links


def _detect_captcha(page) -> bool:
    """Detect common CAPTCHA challenges on the page."""
    captcha_selectors = [
        # reCAPTCHA
        'iframe[src*="recaptcha"]',
        'div.g-recaptcha',
        '#recaptcha',
        # hCaptcha
        'iframe[src*="hcaptcha"]',
        'div.h-captcha',
        # Cloudflare
        '#challenge-form',
        '.cf-challenge-form',
        'div[ray]',
        # Generic
        'img[alt*="captcha" i]',
        'input[name*="captcha"]',
        'div[id*="captcha"]',
        'div[class*="captcha"]',
        # Text-based detection
        'text=/verify you are human/i',
        'text=/captcha/i',
        'text=/challenge/i',
    ]
    for sel in captcha_selectors:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:  # noqa: BLE001, S110
            pass
    return False


_console = Console()

def _wait_for_captcha(page, source_name: str, url: str, console: Console | None = None) -> None:
    """Pause and wait for human to solve CAPTCHA."""
    c = console or _console
    c.print(f"\n[bold yellow]⚠ CAPTCHA detected on {source_name}[/bold yellow]")
    c.print(f"[dim]URL: {url}[/dim]")
    c.print("[bold cyan]Please solve the CAPTCHA in the browser window...[/bold cyan]")
    c.print("[dim]Press Enter in this terminal when done, or wait for auto-detection...[/dim]")

    # Wait for CAPTCHA to disappear (polling)
    start = time.time()
    while time.time() - start < 300:  # 5 minute max
        if not _detect_captcha(page):
            c.print("[green]CAPTCHA appears solved![/green]")
            page.wait_for_timeout(2000)
            return
        time.sleep(3)

    # If timeout, ask for manual confirmation
    c.print("[yellow]Auto-detection timeout. Assuming you solved it.[/yellow]")


def fetch_with_playwright(url: str, source_name: str = "") -> str | None:
    """Fetch page using Playwright headed browser with CAPTCHA handling."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page()
            page.goto(url, timeout=BROWSER_TIMEOUT, wait_until="networkidle")

            # Check for CAPTCHA after initial load
            if _detect_captcha(page):
                _wait_for_captcha(page, source_name, url, _console)

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            # Check again after scroll (some CAPTCHAs appear on interaction)
            if _detect_captcha(page):
                _wait_for_captcha(page, source_name, url, _console)

            html = page.content()
            browser.close()
            return html
    except Exception:  # noqa: BLE001
        return None


def fetch_page(url: str, source_name: str, force_browser: bool = False) -> tuple[str | None, str]:
    """Try static fetch, fallback to Playwright if static fails and force_browser."""
    # Always try static first
    try:
        html = fetch_static(url, timeout=REQUEST_TIMEOUT, retries=REQUEST_RETRIES)
        if html and len(html) > 5000:
            return html, "static"
    except Exception:  # noqa: BLE001, S110
        pass

    if not force_browser:
        return None, "failed"

    ProgressTracker.set_status(source_name, "browser", f"Playwright: {url[:50]}")
    html = fetch_with_playwright(url, source_name)
    if html:
        return html, "playwright"
    return None, "failed"


def extract_articles_from_html(html: str, base_url: str, source_name: str, method: str) -> list[dict]:
    """Extract article data from HTML using ld+json, with NEWS_SCHEMA fallback."""
    soup = parse(html)
    articles = extract_ld_json(soup)

    results = []
    if articles:
        for art in articles:
            title = art.get("headline", "")
            desc = art.get("description", "")
            author = art.get("author", {}).get("name", "") if isinstance(art.get("author"), dict) else art.get("author", "")
            date_pub = art.get("datePublished", "")
            url = art.get("url", "") or art.get("mainEntityOfPage", {}).get("@id", "") if isinstance(art.get("mainEntityOfPage"), dict) else art.get("url", "")
            article_id = art.get("@id", "") or url

            if not title:
                continue

            results.append({
                "source_name": source_name,
                "website_url": base_url,
                "article_url": url,
                "article_id": article_id,
                "title": title,
                "description": desc,
                "author": author,
                "time_published": date_pub,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "source_urls": url,
                "method": f"{method}-ldjson",
            })
    else:
        # Fallback: use NEWS_SCHEMA (CSS selectors)
        try:
            batch = NEWS_SCHEMA.extract(soup, base_url=base_url)
            for rec in batch:
                if not rec.get("title"):
                    continue
                results.append({
                    "source_name": source_name,
                    "website_url": base_url,
                    "article_url": rec.get("source_urls", ""),
                    "article_id": rec.get("article_id", "") or rec.get("id", ""),
                    "title": rec.get("title", ""),
                    "description": rec.get("description", ""),
                    "author": rec.get("author", ""),
                    "time_published": rec.get("published_at", ""),
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "source_urls": rec.get("source_urls", ""),
                    "method": f"{method}-css",
                })
        except Exception:  # noqa: BLE001, S110
            pass

    return results


def collect_source(source: dict) -> list[dict]:
    name = source.get("Source Name", "") or source.get("Website URL", "")
    website_url = source.get("Website URL", "").strip()
    if not website_url.startswith(("http://", "https://")):
        website_url = "https://" + website_url

    ProgressTracker.init(name)
    seen_urls: set[str] = set()
    records: list[dict] = []

    # Build list of URLs to try: homepage + common section pages
    urls_to_try = [website_url]
    for section in ["/news", "/world", "/latest", "/articles", "/stories", "/news/latest"]:
        urls_to_try.append(urljoin(website_url, section))

    for url in urls_to_try:
        if len(records) >= TARGET_PER_SOURCE:
            break

        ProgressTracker.set_status(name, "fetching", f"{url[:60]}")

        html, _method = fetch_page(url, name, force_browser=True)
        if not html:
            ProgressTracker.set_status(name, "error", f"fetch failed: {url[:40]}")
            continue

        soup = parse(html)
        article_links = extract_article_links(soup, website_url, max_links=MAX_LINKS_PER_SECTION)
        ProgressTracker.set_status(name, "parsing", f"found {len(article_links)} links")

        def fetch_article(art_url: str) -> list[dict]:
            art_html, art_method = fetch_page(art_url, name, force_browser=False)
            if not art_html:
                return []
            return extract_articles_from_html(art_html, website_url, name, art_method)

        with ThreadPoolExecutor(max_workers=ARTICLE_WORKERS) as ex:
            futures = {ex.submit(fetch_article, u): u for u in article_links if u not in seen_urls}
            for fut in as_completed(futures):
                if len(records) >= TARGET_PER_SOURCE:
                    break
                art_url = futures[fut]
                if art_url in seen_urls:
                    continue
                seen_urls.add(art_url)
                try:
                    articles = fut.result()
                    for art in articles:
                        if art["article_url"] in seen_urls:
                            continue
                        seen_urls.add(art["article_url"])
                        records.append(art)
                        ProgressTracker.add(name, 1)
                        if len(records) >= TARGET_PER_SOURCE:
                            break
                except Exception:  # noqa: BLE001, S110
                    pass

        time.sleep(1)

    ProgressTracker.set_status(name, "done", f"finished {len(records)}")
    return records[:TARGET_PER_SOURCE]


def main() -> None:
    if not PLAYWRIGHT_AVAILABLE:
        print("ERROR: Playwright not installed. Run: pip install playwright && playwright install chromium")
        return

    sources = read_sources(SOURCES_CSV)
    ensure_output(OUTPUT_CSV)

    printer = threading.Thread(target=status_printer, daemon=True)
    printer.start()

    print(f"Starting {len(sources)} sources, target {TARGET_PER_SOURCE} each (DEMO)...")
    print("Method: static HTTP first, then Playwright headed browser fallback")
    print("Extraction: ld+json preferred, NEWS_SCHEMA CSS fallback")
    print("NOTE: Most news sites block automated article access. See batch_rss_collect.py for production use.\n")

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(collect_source, src): src for src in sources}
            for fut in as_completed(futures):
                src = futures[fut]
                name = src.get("Source Name", "") or src.get("Website URL", "")
                try:
                    recs = fut.result()
                    append_rows(OUTPUT_CSV, recs)
                except Exception as exc:  # noqa: BLE001
                    ProgressTracker.set_status(name, "error", f"fatal: {exc}"[:80])
    except KeyboardInterrupt:
        print("\nInterrupted...")
    finally:
        time.sleep(0.3)
        data = ProgressTracker.get_all()
        total = sum(d["count"] for d in data.values())
        print(f"\nDone. Total saved: {total}")
        print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()