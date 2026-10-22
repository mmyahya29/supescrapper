#!/usr/bin/env python3
"""CLI entry point for supescrapper."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from rich.console import Console
from rich.table import Table

from .classifier import INTERACTIVE_TYPES, get_classifier
from .exceptions import SupescrapperError
from .schema import (
    get_schema,
    load_schema_from_json,
    register_schema,
)
from .scraper import Scraper


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="supescrapper",
        description="Universal web DOM scraper — fetch, query, extract, export.",
    )
    p.add_argument("url", nargs="?", help="URL to scrape")
    p.add_argument("--html-file", "-f", help="Local HTML file to parse instead of URL")
    p.add_argument("--select", "-s", help="CSS selector (default: entire page)")
    p.add_argument("--select-xpath", help="XPath selector")
    p.add_argument("--select-regex", help="Regex to match element text")
    p.add_argument("--search", help="Full-text search: find all elements containing this term")
    p.add_argument("--mode", choices=["css", "xpath", "regex"], default="css", help="Selector mode")
    p.add_argument("--extract", "-e", nargs="*", metavar="KEY=SPEC", help='Extract named fields, e.g. title="h2.text" price="span.price.text"')
    p.add_argument("--dynamic", "-d", action="store_true", help="Force Playwright (skip auto-detection)")
    p.add_argument("--static", action="store_true", help="Force static fetch only (never use Playwright)")
    p.add_argument("--wait-until", default="domcontentloaded", help="Playwright wait condition (default: domcontentloaded)")
    p.add_argument("--scroll", type=int, default=0, help="Wheel-scroll down N times to trigger lazy loading (Playwright)")
    p.add_argument("--wait-for", help="CSS selector to wait for before capturing HTML (Playwright)")
    p.add_argument("--wait-ms", type=int, help="Fixed wait in milliseconds after navigation (Playwright)")
    p.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds (default: 30)")
    p.add_argument("--retries", type=int, default=3, help="Number of retries (default: 3)")
    p.add_argument("--user-agent", help="Custom user agent string")
    p.add_argument("--proxy", help="HTTP proxy URL")
    p.add_argument("--impersonate", nargs="?", const="chrome124", metavar="TARGET",
                   help="Use curl_cffi TLS impersonation (optional browser target, default chrome124)")
    p.add_argument("--proxy-pool", nargs="+", metavar="URL",
                   help="Rotating proxy pool (used with --impersonate or crawl)")
    p.add_argument("--where-text", nargs=2, metavar=("OP", "VALUE"), help='Filter by text: "contains|equals|matches" value')
    p.add_argument("--where-attr", nargs=3, metavar=("ATTR", "OP", "VALUE"), help='Filter by attr: attr "contains|equals|matches" value')
    p.add_argument("--where-tag", help="Filter by tag name")
    p.add_argument("--limit", "-n", type=int, help="Limit number of results")
    p.add_argument("--config", metavar="PATH", help="Run a scrape from a JSON/TOML config file (sources, limits, date range, export)")
    p.add_argument("--export", choices=["json", "csv", "sqlite", "xlsx", "xls"], help="Export format")
    p.add_argument("--output", "-o", help="Output file path (required when --export is set)")
    p.add_argument("--table", action="store_true", help="Print results as a table in the terminal")
    p.add_argument("--raw", action="store_true", help="Print raw JSON to stdout")
    p.add_argument("--extract-links", action="store_true", help="Extract all links from page")
    p.add_argument("--extract-images", action="store_true", help="Extract all images from page")
    p.add_argument("--extract-table", nargs="?", const="table", help="Extract HTML table (optional selector)")
    p.add_argument("--schema", metavar="NAME", help="Apply a named schema (e.g. news, article, product) for structured extraction")
    p.add_argument("--schema-file", metavar="PATH", help="Load a schema definition from a JSON file and apply it")
    p.add_argument("--strict-schema", action="store_true", help="Raise if required schema fields are missing")
    p.add_argument("--list-schemas", action="store_true", help="List registered schemas and exit")

    # --- Crawl (Level 4) ---
    crawl = p.add_argument_group("crawl")
    crawl.add_argument("--crawl", action="store_true", help="Crawl from URL, following links")
    crawl.add_argument("--concurrency", type=int, default=8, help="Concurrent crawl requests (default: 8)")
    crawl.add_argument("--max-urls", type=int, default=1000, help="Maximum URLs to crawl (default: 1000)")
    crawl.add_argument("--max-depth", type=int, help="Maximum link depth from the seed")
    crawl.add_argument("--download-delay", type=float, default=0.0, help="Delay between requests to the same host (seconds)")
    crawl.add_argument("--autothrottle", action="store_true", help="Adaptively adjust delay based on server response")
    crawl.add_argument("--allow-domains", nargs="+", metavar="DOMAIN", help="Restrict crawl to these domains")
    crawl.add_argument("--deny-patterns", nargs="+", metavar="REGEX", help="Skip URLs matching these regexes")
    crawl.add_argument("--allow-patterns", nargs="+", metavar="REGEX", help="Only crawl URLs matching these regexes")
    crawl.add_argument("--follow-selector", metavar="CSS", help="CSS selector for 'next page' links (pagination)")

    # --- Element detection ---
    detect = p.add_argument_group("element detection")
    detect.add_argument("--find", metavar="TYPE", help="Find all elements of a specific type (e.g., button, search_input, login_form)")
    detect.add_argument("--find-interactive", action="store_true", help="Find all interactive elements (buttons, inputs, links, etc.)")
    detect.add_argument("--classify", action="store_true", help="Classify all query results with element types")
    detect.add_argument("--list-types", action="store_true", help="List all supported element types and exit")

    # --- News & RSS Feeds ---
    feeds_grp = p.add_argument_group("feeds")
    feeds_grp.add_argument("--feeds", nargs="+", metavar="URL", help="Scrape multiple RSS/Atom feeds simultaneously")
    feeds_grp.add_argument("--feeds-file", metavar="PATH", help="File containing list of feed URLs (one per line)")
    feeds_grp.add_argument("--feed-concurrency", type=int, default=8, help="Concurrent feed workers (default: 8)")

    # --- News Articles from Website ---
    news_grp = p.add_argument_group("news articles")
    news_grp.add_argument("--news", action="store_true", help="Extract news articles from a website (ld+json + RSS fallback)")
    news_grp.add_argument("--target", type=int, default=20, help="Target number of articles to collect (default: 20)")
    news_grp.add_argument("--max-pages", type=int, default=5, help="Max section pages to scan for article links (default: 5)")
    news_grp.add_argument("--no-ldjson", action="store_true", help="Skip ld+json extraction, use CSS only")

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.list_schemas:
        from .schema import list_schemas as _list_schemas
        console.print("[bold]Registered schemas:[/bold]")
        for name in _list_schemas():
            try:
                s = get_schema(name)
                console.print(f"  [cyan]{name}[/cyan] — {s.description or 'no description'}")
            except Exception:  # noqa: BLE001
                console.print(f"  [cyan]{name}[/cyan]")
        return

    if args.list_types:
        classifier = get_classifier()
        console.print("[bold]Supported element types:[/bold]")
        for name in sorted(set(classifier.registered_types)):
            console.print(f"  [cyan]{name}[/cyan]")
        console.print(f"\n[dim]Interactive types: {', '.join(sorted(INTERACTIVE_TYPES))}[/dim]")
        return

    if not args.url and not args.html_file and not args.feeds and not args.feeds_file:
        parser.print_help()
        sys.exit(1)

    try:
        _run(args, console)
    except SupescrapperError as exc:
        console.print(f"[bold red]{type(exc).__name__}:[/bold red] {exc}")
        sys.exit(1)
def _apply_filters(scraper: Scraper, args) -> None:
    """Apply --where-* and --limit to the currently active result set."""
    if args.where_text:
        scraper.where_text(args.where_text[0], args.where_text[1])
    if args.where_attr:
        scraper.where_attr(args.where_attr[0], args.where_attr[1], args.where_attr[2])
    if args.where_tag:
        scraper.where_tag(args.where_tag)
    if args.limit:
        scraper.limit(args.limit)


def _run(args, console: Console) -> None:
    if args.config:
        from .feeds import load_config, run_config

        cfg = load_config(args.config)
        results = run_config(cfg)
        console.print(f"\n[bold green]{len(results)} records found.[/bold green]")
        if args.export or cfg.export:
            out = args.output or cfg.output or "results.csv"
            fmt = args.export or cfg.export or "csv"
            path = results.export(fmt, out)
            console.print(f"[bold green]Exported to:[/bold green] {path}")
        elif args.raw:
            console.print_json(results.to_json())
        else:
            if not results:
                console.print("[yellow]No results.[/yellow]")
                return
            from rich.table import Table
            table = Table(show_header=True, header_style="bold magenta")
            for col in results.keys()[:12]:
                table.add_column(col, max_width=40, overflow="fold")
            for rec in results:
                row = [str(rec.get(c, ""))[:120] for c in results.keys()[:12]]
                table.add_row(*row)
            console.print(table)
        return

    if args.crawl:
        _run_crawl(args, console)
        return

    if args.feeds or args.feeds_file:
        _run_feeds(args, console)
        return

    if args.news:
        _run_news(args, console)
        return

    scraper = Scraper()

    if args.html_file:
        html = Path(args.html_file).read_text(encoding="utf-8")
        scraper.from_html(html)
    else:
        fetch_kwargs: dict = {}
        if args.timeout != 30:
            fetch_kwargs["timeout"] = args.timeout
        if args.retries != 3:
            fetch_kwargs["retries"] = args.retries
        if args.user_agent:
            fetch_kwargs["user_agent"] = args.user_agent
        if args.proxy:
            fetch_kwargs["proxies"] = {"http": args.proxy, "https": args.proxy}
        if args.impersonate:
            fetch_kwargs["impersonate"] = args.impersonate
        if args.proxy_pool:
            fetch_kwargs["proxy_pool"] = args.proxy_pool
        # dynamic: default is "auto", --dynamic forces True, --static forces False
        if args.dynamic:
            fetch_kwargs["dynamic"] = True
            fetch_kwargs["wait_until"] = args.wait_until
        elif args.static:
            fetch_kwargs["dynamic"] = False
        else:
            fetch_kwargs["dynamic"] = "auto"
        if fetch_kwargs.get("dynamic") is not False:
            if args.scroll:
                fetch_kwargs["scroll"] = args.scroll
            if args.wait_for:
                fetch_kwargs["wait_for"] = args.wait_for
            if args.wait_ms:
                fetch_kwargs["wait_ms"] = args.wait_ms
        console.print(f"[bold cyan]Fetching:[/bold cyan] {args.url}")
        scraper.fetch(args.url, **fetch_kwargs)

    # --- Schema extraction (structured) ---
    schema_name = None
    if args.schema_file:
        sch = load_schema_from_json(args.schema_file)
        register_schema(sch, replace=True)
        schema_name = sch.name
    elif args.schema:
        schema_name = args.schema
    if schema_name:
        if args.select or args.select_xpath or args.select_regex or args.search:
            if args.search:
                scraper.search(args.search)
            elif args.select_xpath:
                scraper.query(args.select_xpath, mode="xpath")
            elif args.select_regex:
                scraper.query(args.select_regex, mode="regex")
            elif args.select:
                scraper.query(args.select, mode=args.mode)
        _apply_filters(scraper, args)
        results = scraper.scrape(schema_name, strict=args.strict_schema)
        _output_schema(results, args, console)
        return

    # --- Element detection shortcuts ---
    if args.find:
        scraper.find_elements(args.find)
        _apply_filters(scraper, args)
        _output(scraper.extract(), args, console)
        return
    if args.find_interactive:
        scraper.find_interactive()
        _apply_filters(scraper, args)
        _output(scraper.extract(), args, console)
        return

    # --- Extract shortcuts ---
    if args.extract_links:
        results = scraper.extract_links()
        _output(results, args, console)
        return
    if args.extract_images:
        results = scraper.extract_images()
        _output(results, args, console)
        return
    if args.extract_table is not None:
        table_data = scraper.extract_table(args.extract_table)
        console.print_json(_list_of_dicts_to_json(table_data))
        return

    # --- Search mode ---
    if args.search:
        scraper.search(args.search)
    # --- Selector ---
    elif args.select or args.select_xpath or args.select_regex:
        selector = args.select
        mode = args.mode
        if args.select_xpath:
            selector = args.select_xpath
            mode = "xpath"
        elif args.select_regex:
            selector = args.select_regex
            mode = "regex"
        scraper.query(selector, mode=mode)
    else:
        return

    # --- Filters ---
    _apply_filters(scraper, args)

    # --- Classify ---
    if args.classify:
        scraper.classify()

    # --- Extract named fields ---
    field_selectors: dict[str, str] = {}
    if args.extract:
        for item in args.extract:
            if "=" in item:
                k, v = item.split("=", 1)
                field_selectors[k] = v.strip('"').strip("'")

    if field_selectors:
        results = scraper.extract(**field_selectors)
    else:
        results = scraper.extract()

    _output(results, args, console)


def _run_feeds(args, console: Console) -> None:
    from .feeds import scrape_feeds

    urls: list[str] = []
    if args.feeds:
        urls.extend(args.feeds)
    if args.feeds_file:
        p = Path(args.feeds_file)
        if not p.is_file():
            console.print(f"[red]Feeds file not found: {args.feeds_file}[/red]")
            sys.exit(1)
        urls.extend([line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")])

    if not urls:
        console.print("[red]No feed URLs specified via --feeds or --feeds-file.[/red]")
        sys.exit(1)

    schema_name = "rss"
    if args.schema_file:
        sch = load_schema_from_json(args.schema_file)
        register_schema(sch, replace=True)
        schema_name = sch.name
    elif args.schema:
        schema_name = args.schema

    console.print(f"[bold cyan]Scraping {len(urls)} feed(s) simultaneously (schema={schema_name})...[/bold cyan]")
    results = scrape_feeds(
        urls,
        schema=schema_name,
        concurrency=args.feed_concurrency,
        timeout=args.timeout,
        retries=args.retries,
        user_agent=args.user_agent,
        impersonate=args.impersonate,
        strict=args.strict_schema,
    )
    _output_schema(results, args, console)


def _run_news(args, console: Console) -> None:
    """Extract news articles from a website using ld+json with RSS fallback."""

    from .fetcher import fetch_static
    from .parser import is_xml, parse
    from .schema import get_schema

    if not args.url:
        console.print("[red]--news requires a URL.[/red]")
        sys.exit(1)

    target = args.target
    max_pages = args.max_pages
    use_ldjson = not args.no_ldjson

    console.print(f"[bold cyan]Extracting up to {target} news articles from:[/bold cyan] {args.url}")

    # Step 1: Try to find RSS feed from the URL
    base_url = args.url
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    rss_urls = _discover_rss_feeds(base_url, console)
    article_urls: list[str] = []
    seen_urls: set[str] = set()

    # Step 2: Get article URLs from RSS feeds
    for rss_url in rss_urls:
        if len(article_urls) >= target * 2:  # Get extra for filtering
            break
        try:
            html = fetch_static(rss_url, timeout=args.timeout, retries=args.retries)
            if is_xml(html):
                soup = parse(html)
                # Use rss schema to extract article URLs
                rss_sch = get_schema("rss")
                batch = rss_sch.extract(soup, base_url=rss_url)
                for rec in batch:
                    url = rec.get("source_urls") or rec.get("url") or rec.get("link")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        article_urls.append(url)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Failed to fetch RSS {rss_url}: {exc}[/yellow]")

    # Step 3: If not enough from RSS, scan homepage/section pages for article links
    if len(article_urls) < target:
        console.print(f"[yellow]Only {len(article_urls)} articles from RSS, scanning website for more...[/yellow]")
        article_urls.extend(_scan_for_article_links(base_url, max_pages, target - len(article_urls), args, console, seen_urls))

    if not article_urls:
        console.print("[red]No article URLs found.[/red]")
        return

    console.print(f"[bold cyan]Fetching {min(len(article_urls), target)} article pages...[/bold cyan]")

    # Step 4: Fetch each article page and extract using ld+json or CSS
    records = _fetch_and_extract_articles(
        article_urls[:target],
        target,
        use_ldjson,
        args,
        console
    )

    if not records:
        console.print("[yellow]No articles extracted.[/yellow]")
        return

    console.print(f"\n[bold green]{len(records)} articles extracted.[/bold green]")

    # Output
    if args.export:
        if not args.output:
            console.print("[red]--output is required when using --export.[/red]")
            sys.exit(1)
        # Convert to list of dicts for export
        from .exporters import export_records
        fmt = args.export
        if fmt == "xls":
            fmt = "xlsx"
        path = export_records(records, fmt, args.output)
        console.print(f"[bold green]Exported to:[/bold green] {path}")
    elif args.raw:
        import json
        console.print_json(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        _print_table(records, console)


def _discover_rss_feeds(base_url: str, console: Console) -> list[str]:
    """Discover RSS feed URLs from a website."""

    from .fetcher import fetch_static
    from .parser import is_xml, parse

    rss_urls: list[str] = []
    try:
        html = fetch_static(base_url, timeout=15, retries=2)
        soup = parse(html)

        # Look for <link rel="alternate" type="application/rss+xml">
        for link in soup.find_all("link", rel="alternate"):
            href = link.get("href")
            if href and ("rss" in link.get("type", "") or "xml" in link.get("type", "")):
                if not href.startswith(("http://", "https://")):
                    href = urljoin(base_url, href)
                rss_urls.append(href)

        # Also check for common RSS paths
        common_paths = ["/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml", "/rss/", "/feed/"]
        for path in common_paths:
            test_url = urljoin(base_url, path)
            if test_url not in rss_urls:
                try:
                    test_html = fetch_static(test_url, timeout=10, retries=1)
                    if is_xml(test_html):
                        rss_urls.append(test_url)
                except Exception:  # noqa: BLE001, S110
                    pass

    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]RSS discovery failed: {exc}[/yellow]")

    return rss_urls[:5]  # Limit to 5 feeds


def _scan_for_article_links(base_url: str, max_pages: int, needed: int, args, console: Console, seen_urls: set[str]) -> list[str]:
    """Scan homepage and section pages for article links."""
    from urllib.parse import urljoin

    from .fetcher import fetch_static
    from .parser import parse

    article_urls: list[str] = []
    section_paths = ["/news", "/world", "/latest", "/articles", "/stories", "/news/latest", "/news/world", "/business", "/politics", "/technology"]
    pages_to_try = [base_url] + [urljoin(base_url, p) for p in section_paths[:max_pages - 1]]

    for url in pages_to_try:
        if len(article_urls) >= needed:
            break
        try:
            html = fetch_static(url, timeout=args.timeout, retries=args.retries)
            soup = parse(html)

            # Common article link patterns
            selectors = [
                "a[href*='/news/articles/']",  # BBC
                "a[href*='/news/videos/']",
                "a[href*='/2024/']", "a[href*='/2025/']", "a[href*='/2026/']",
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
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    article_urls.append(href)
                    if len(article_urls) >= needed:
                        break
                if len(article_urls) >= needed:
                    break

        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Failed to scan {url}: {exc}[/yellow]")

    return article_urls


def _fetch_and_extract_articles(article_urls: list[str], target: int, use_ldjson: bool, args, console: Console) -> list[dict]:
    """Fetch article pages and extract data using ld+json or CSS fallback."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timezone

    from .fetcher import fetch_static
    from .parser import parse
    from .schema import NEWS_SCHEMA, extract_ld_json

    def _extract_full_text(soup) -> str:
        """Extract full article text content from common article containers."""
        selectors = [
            "article",
            "[itemprop='articleBody']",
            ".article-body",
            ".post-content",
            ".entry-content",
            ".article-content",
            ".content",
            "main",
            ".story-body",
            ".article__body",
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(" ", strip=True)
                if len(text) > 200:  # substantial content
                    return text
        # Fallback: get all paragraph text
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        return text

    def _truncate_words(text: str, max_words: int = 1000) -> str:
        """Truncate text to max_words."""
        words = text.split()
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words]) + "..."

    def _get_author_name(author_data: Any) -> str:
        """Extract author name from string or object."""
        if not author_data:
            return ""
        if isinstance(author_data, str):
            return author_data
        if isinstance(author_data, dict):
            # Handle Schema.org Person object: {"@type": "Person", "name": "John"}
            return author_data.get("name", "")
        if isinstance(author_data, list):
            # Array of authors
            names = []
            for a in author_data:
                if isinstance(a, str):
                    names.append(a)
                elif isinstance(a, dict):
                    names.append(a.get("name", ""))
            return ", ".join(filter(None, names))
        return ""

    def fetch_one(url: str) -> list[dict]:
        try:
            html = fetch_static(url, timeout=args.timeout, retries=args.retries)
            soup = parse(html)
            results = []

            if use_ldjson:
                ld_items = extract_ld_json(soup)
                for art in ld_items:
                    title = art.get("headline", "")
                    if not title:
                        continue

                    # Get full article text
                    full_text = _extract_full_text(soup)
                    description = art.get("description", "")
                    # Use full text if description is short, otherwise use description
                    if len(full_text.split()) > len(description.split()):
                        content = _truncate_words(full_text, 1000)
                    else:
                        content = _truncate_words(description, 1000)

                    author = _get_author_name(art.get("author"))

                    results.append({
                        "source_url": url,
                        "title": title,
                        "description": content,
                        "author": author,
                        "time_published": art.get("datePublished", ""),
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                        "method": "ldjson",
                    })

            if not results:
                # CSS fallback
                batch = NEWS_SCHEMA.extract(soup, base_url=url)
                for rec in batch:
                    if rec.get("title"):
                        full_text = _extract_full_text(soup)
                        description = _truncate_words(full_text, 1000)
                        author = _get_author_name(rec.get("author"))

                        results.append({
                            "source_url": rec.get("source_urls", url),
                            "title": rec.get("title", ""),
                            "description": description,
                            "author": author,
                            "time_published": rec.get("published_at", ""),
                            "scraped_at": datetime.now(timezone.utc).isoformat(),
                            "method": "css",
                        })
            return results
        except Exception:  # noqa: BLE001
            return []

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_one, u): u for u in article_urls}
        for fut in as_completed(futures):
            if len(records) >= target:
                break
            res = fut.result()
            for r in res:
                if len(records) >= target:
                    break
                records.append(r)
    return records


def _run_crawl(args, console: Console) -> None:
    from .crawler import DEFAULT_USER_AGENT, CrawlConfig, CrawlEngine
    from .scraper import Scraper as _Scraper

    if not args.url:
        console.print("[red]--crawl requires a URL.[/red]")
        sys.exit(1)

    config = CrawlConfig(
        max_urls=args.max_urls,
        concurrency=args.concurrency,
        download_delay=args.download_delay,
        autothrottle=args.autothrottle,
        retries=args.retries,
        timeout=args.timeout,
        max_depth=args.max_depth,
        user_agent=args.user_agent or DEFAULT_USER_AGENT,
        impersonate=args.impersonate or None,
        proxy_pool=args.proxy_pool or [],
        allowed_domains=args.allow_domains or [],
        deny_patterns=args.deny_patterns or [],
        allow_patterns=args.allow_patterns or [],
        dynamic=bool(args.dynamic),
        follow_selector=args.follow_selector,
        schema=args.schema or None,
        strict_schema=args.strict_schema,
    )

    if args.schema_file and not args.schema:
        from .schema import load_schema_from_json, register_schema
        sch = load_schema_from_json(args.schema_file)
        register_schema(sch, replace=True)
        config.schema = sch.name
    elif args.schema:
        config.schema = args.schema

    # Build items using the selector/extract arguments if provided.
    selector = args.select
    mode = args.mode
    if args.select_xpath:
        selector, mode = args.select_xpath, "xpath"
    elif args.select_regex:
        selector, mode = args.select_regex, "regex"

    field_selectors: dict[str, str] = {}
    if args.extract:
        for spec in args.extract:
            if "=" in spec:
                k, v = spec.split("=", 1)
                field_selectors[k] = v.strip('"').strip("'")

    def item_builder(soup):
        sub = _Scraper().from_html(str(soup))
        try:
            if selector:
                sub.query(selector, mode=mode)
            if field_selectors:
                return list(sub.extract(**field_selectors))
            if selector:
                return list(sub.extract())
        except Exception:  # noqa: BLE001
            return []
        return []

    engine = CrawlEngine(config)
    console.print(f"[bold cyan]Crawling seed:[/bold cyan] {args.url}")
    crawl_results = list(engine.crawl(args.url, item_builder=item_builder if (selector or field_selectors) else None))

    items = []
    ok = sum(1 for r in crawl_results if r.status == "ok")
    err = sum(1 for r in crawl_results if r.status != "ok")
    for r in crawl_results:
        for it in r.items:
            if not it.url:
                it.url = r.url
            items.append(it)

    console.print(f"\n[bold green]Crawled {len(crawl_results)} URLs "
                  f"({ok} ok, {err} errors), {len(items)} items.[/bold green]")

    if args.export:
        if not args.output:
            console.print("[red]--output is required when using --export.[/red]")
            sys.exit(1)
        from .models import ScrapedResultSet
        rs = ScrapedResultSet(items)
        path = rs.export(args.export, args.output)
        console.print(f"[bold green]Exported to:[/bold green] {path}")


def _output_schema(results, args, console: Console) -> None:
    console.print(f"\n[bold green]{len(results)} records found.[/bold green]")
    if args.export:
        if not args.output:
            console.print("[red]--output is required when using --export.[/red]")
            sys.exit(1)
        path = results.export(args.export, args.output)
        console.print(f"[bold green]Exported to:[/bold green] {path}")
    elif args.raw:
        console.print_json(results.to_json())
    else:
        if not results:
            console.print("[yellow]No results.[/yellow]")
            return
        from rich.table import Table
        table = Table(show_header=True, header_style="bold magenta")
        for col in results.keys()[:12]:
            table.add_column(col, max_width=40, overflow="fold")
        for rec in results:
            row = [str(rec.get(c, ""))[:120] for c in results.keys()[:12]]
            table.add_row(*row)
        console.print(table)


def _output(results, args, console: Console) -> None:
    console.print(f"\n[bold green]{len(results)} results found.[/bold green]")

    if args.export:
        if not args.output:
            console.print("[red]--output is required when using --export.[/red]")
            sys.exit(1)
        path = results.export(args.export, args.output)
        console.print(f"[bold green]Exported to:[/bold green] {path}")
    elif args.raw:
        console.print_json(results.to_json())
    else:
        _print_table(results, console)


def _print_table(results, console: Console) -> None:
    if not results:
        console.print("[yellow]No results.[/yellow]")
        return
    sample = results[0]
    if hasattr(sample, "to_dict"):
        sample = sample.to_dict()
    table = Table(show_header=True, header_style="bold magenta")
    for col in list(sample.keys())[:8]:
        table.add_column(col, max_width=40, overflow="fold")
    for item in results:
        d = item.to_dict() if hasattr(item, "to_dict") else item
        row = [str(d.get(c, ""))[:80] for c in list(sample.keys())[:8]]
        table.add_row(*row)
    console.print(table)


def _list_of_dicts_to_json(data: list[dict]) -> str:
    import json
    return json.dumps(data, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
