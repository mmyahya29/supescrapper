#!/usr/bin/env python3
"""
RSS + Google News fallback collector.
1. Try provided RSS feed
2. If < TARGET_PER_SOURCE, supplement with Google News site:domain.com across time windows
Target: 100 articles per source = 1000 total.
"""

from __future__ import annotations

import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from supescrapper.fetcher import fetch_static
from supescrapper.parser import parse
from supescrapper.schema import get_schema

# ┌──────────────────────────────────────────────────────────────────────────┐
# │ CONFIGURATION                                                            │
# └──────────────────────────────────────────────────────────────────────────┘
SOURCES_CSV = "sources.csv"
OUTPUT_CSV = "news_output_1000.csv"
TARGET_PER_SOURCE = 100
WORKERS = 10
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
STATUS_INTERVAL = 1.0

GN_TIME_WINDOWS = ["1d", "7d", "30d", "1y"]
# ┌──────────────────────────────────────────────────────────────────────────┐
# │ END CONFIGURATION                                                        │
# └──────────────────────────────────────────────────────────────────────────┘

FIELDNAMES = [
    "source_name",
    "website_url",
    "rss_url",
    "article_id",
    "title",
    "description",
    "author",
    "time_published",
    "scraped_at",
    "source_urls",
    "query",
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
        print(f"=== RSS + GNews Fallback  ({time.strftime('%H:%M:%S')}) ===")
        print(f"{'Source':<28} {'Status':<12} {'Count/100':>10}  Detail")
        print("-" * 90)
        for name in sorted(data.keys()):
            d = data[name]
            stuck = " <-- STUCK?" if (time.time() - d["last_update"]) > 20 else ""
            print(f"{name[:27]:<28} {d['status']:<12} {d['count']:>3}/100  {d['detail']}{stuck}")
        print("-" * 90)
        done = sum(1 for d in data.values() if d["status"] == "done")
        total = sum(d["count"] for d in data.values())
        print(f"Done: {done}/{len(data)}  |  Total: {total}/1000")


def load_site_configs() -> None:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from site_configs.registry import load_all
    loaded = load_all()
    names = [s.name for s in loaded]
    print(f"[CONFIG] loaded {len(loaded)} site schemas: {', '.join(names) or 'none'}")


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


def gn_url(domain: str, window: str) -> str:
    """Build Google News RSS URL for a domain + time window."""
    base = "https://news.google.com/rss/search"
    qs = {"q": f"site:{domain} when:{window}", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return base + "?" + urlencode(qs)


def is_google_news_url(url: str) -> bool:
    return "news.google.com/rss" in url


def extract_from_url(url: str, schema_name: str, seen_ids: set[str], max_add: int) -> tuple[list[dict], int]:
    """Fetch URL, extract with schema, return new records and count added."""
    try:
        html = fetch_static(url, timeout=REQUEST_TIMEOUT, retries=REQUEST_RETRIES)
    except Exception:  # noqa: BLE001
        return [], 0

    try:
        soup = parse(html)
        sch = get_schema(schema_name)
        batch = sch.extract(soup, base_url=url)
    except Exception:  # noqa: BLE001
        return [], 0

    new_recs = []
    for rec in batch:
        key = rec.get("article_id") or rec.get("source_urls") or rec.get("title") or ""
        if not key or key in seen_ids:
            continue
        seen_ids.add(key)
        new_recs.append(rec)
        if len(new_recs) >= max_add:
            break
    return new_recs, len(new_recs)


def normalize_rec(source: dict, rec: dict, fetch_url: str, schema_name: str) -> dict:
    return {
        "source_name": source.get("Source Name", ""),
        "website_url": source.get("Website URL", ""),
        "rss_url": fetch_url,
        "article_id": rec.get("article_id", ""),
        "title": rec.get("title", ""),
        "description": rec.get("description", ""),
        "author": rec.get("author_name", ""),
        "time_published": rec.get("date_published", ""),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source_urls": rec.get("source_urls", ""),
        "query": schema_name,
    }


def collect_source(source: dict) -> list[dict]:
    name = source.get("Source Name", "") or source.get("Website URL", "") or source.get("Main RSS Feed URL", "")
    rss_url = source.get("Main RSS Feed URL", "").strip()
    domain = source.get("Website URL", "").strip().rstrip("/")
    schema_name = source.get("Schema", "rss")

    ProgressTracker.init(name)
    seen_ids: set[str] = set()
    records: list[dict] = []

    # Stage 1: Primary RSS feed
    if rss_url:
        ProgressTracker.set_status(name, "rss-primary", rss_url[:50])
        new_recs, added = extract_from_url(rss_url, schema_name, seen_ids, TARGET_PER_SOURCE)
        for r in new_recs:
            records.append(normalize_rec(source, r, rss_url, schema_name))
        ProgressTracker.add(name, added)
        ProgressTracker.set_status(name, "rss-primary", f"+{added}")

    # Stage 2: Google News fallback if needed
    if len(records) < TARGET_PER_SOURCE and domain:
        need = TARGET_PER_SOURCE - len(records)
        ProgressTracker.set_status(name, "gn-fallback", f"need {need} more")
        for window in GN_TIME_WINDOWS:
            if len(records) >= TARGET_PER_SOURCE:
                break
            gn = gn_url(domain, window)
            # Use 'rss' schema for Google News (generic RSS structure)
            new_recs, added = extract_from_url(gn, "rss", seen_ids, TARGET_PER_SOURCE - len(records))
            for r in new_recs:
                records.append(normalize_rec(source, r, gn, "rss"))
            ProgressTracker.add(name, added)
            ProgressTracker.set_status(name, "gn-fallback", f"+{added} from when:{window}")
            if added == 0:
                break
            time.sleep(0.5)

    ProgressTracker.set_status(name, "done", f"finished {len(records)}")
    return records[:TARGET_PER_SOURCE]


def main() -> None:
    load_site_configs()
    sources = read_sources(SOURCES_CSV)
    ensure_output(OUTPUT_CSV)

    printer = threading.Thread(target=status_printer, daemon=True)
    printer.start()

    print(f"Starting {len(sources)} sources, target {TARGET_PER_SOURCE} each = {len(sources)*TARGET_PER_SOURCE} total...")
    print("Press Ctrl+C to stop early.\n")

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