#!/usr/bin/env python3
"""CLI entry point for supescrapper."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .scraper import Scraper
from .classifier import get_classifier, INTERACTIVE_TYPES


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
    p.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds (default: 30)")
    p.add_argument("--retries", type=int, default=3, help="Number of retries (default: 3)")
    p.add_argument("--user-agent", help="Custom user agent string")
    p.add_argument("--proxy", help="HTTP proxy URL")
    p.add_argument("--where-text", nargs=2, metavar=("OP", "VALUE"), help='Filter by text: "contains|equals|matches" value')
    p.add_argument("--where-attr", nargs=3, metavar=("ATTR", "OP", "VALUE"), help='Filter by attr: attr "contains|equals|matches" value')
    p.add_argument("--where-tag", help="Filter by tag name")
    p.add_argument("--limit", "-n", type=int, help="Limit number of results")
    p.add_argument("--export", choices=["json", "csv", "sqlite"], help="Export format")
    p.add_argument("--output", "-o", help="Output file path (required when --export is set)")
    p.add_argument("--table", action="store_true", help="Print results as a table in the terminal")
    p.add_argument("--raw", action="store_true", help="Print raw JSON to stdout")
    p.add_argument("--extract-links", action="store_true", help="Extract all links from page")
    p.add_argument("--extract-images", action="store_true", help="Extract all images from page")
    p.add_argument("--extract-table", nargs="?", const="table", help="Extract HTML table (optional selector)")

    # --- Element detection ---
    detect = p.add_argument_group("element detection")
    detect.add_argument("--find", metavar="TYPE", help="Find all elements of a specific type (e.g., button, search_input, login_form)")
    detect.add_argument("--find-interactive", action="store_true", help="Find all interactive elements (buttons, inputs, links, etc.)")
    detect.add_argument("--classify", action="store_true", help="Classify all query results with element types")
    detect.add_argument("--list-types", action="store_true", help="List all supported element types and exit")

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.list_types:
        classifier = get_classifier()
        console.print("[bold]Supported element types:[/bold]")
        for name in sorted(set(classifier.registered_types)):
            console.print(f"  [cyan]{name}[/cyan]")
        console.print(f"\n[dim]Interactive types: {', '.join(sorted(INTERACTIVE_TYPES))}[/dim]")
        return

    if not args.url and not args.html_file:
        parser.print_help()
        sys.exit(1)

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
        # dynamic: default is "auto", --dynamic forces True, --static forces False
        if args.dynamic:
            fetch_kwargs["dynamic"] = True
            fetch_kwargs["wait_until"] = args.wait_until
        elif args.static:
            fetch_kwargs["dynamic"] = False
        else:
            fetch_kwargs["dynamic"] = "auto"
        console.print(f"[bold cyan]Fetching:[/bold cyan] {args.url}")
        scraper.fetch(args.url, **fetch_kwargs)

    # --- Element detection shortcuts ---
    if args.find:
        results = scraper.find_elements(args.find)
        _output(results, args, console)
        return
    if args.find_interactive:
        results = scraper.find_interactive()
        _output(results, args, console)
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
    if args.where_text:
        scraper.where_text(args.where_text[0], args.where_text[1])
    if args.where_attr:
        scraper.where_attr(args.where_attr[0], args.where_attr[1], args.where_attr[2])
    if args.where_tag:
        scraper.where_tag(args.where_tag)
    if args.limit:
        scraper.limit(args.limit)

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
    sample = results[0].to_dict()
    table = Table(show_header=True, header_style="bold magenta")
    for col in list(sample.keys())[:8]:
        table.add_column(col, max_width=40, overflow="fold")
    for item in results:
        d = item.to_dict()
        row = [str(d.get(c, ""))[:80] for c in list(sample.keys())[:8]]
        table.add_row(*row)
    console.print(table)


def _list_of_dicts_to_json(data: list[dict]) -> str:
    import json
    return json.dumps(data, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
