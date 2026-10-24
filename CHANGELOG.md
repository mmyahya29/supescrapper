# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Declarative scraping schemas** (`supescrapper.schema`): `Schema` /
  `Field` describe record roots and per-field extraction (CSS / XPath /
  regex, attributes, regex captures, transforms, many-valued fields).
  Schemas register by name and are applied with `Scraper.scrape()` (sync and
  `AsyncScraper`) or `SchemaResultSet` export (JSON / CSV / SQLite).
- **Built-in schemas**: `news`, `article`, `product` and `rss` (aliased
  `feed`, `news_feed`) — RSS 2.0 and Atom feed items with `article_id`,
  `title`, `description`, `source_urls`, `author_name`, `date_published`
  (RFC 822 / ISO 8601 parsed) plus `scraped_at`, `time_scraped` and
  `source_url` on every record.
- **`supescrapper.feeds`**: `scrape_feeds()` / `async_scrape_feeds()` scrape
  many feeds concurrently (thread pool / asyncio) and `Scraper.scrape_feeds()`
  classmethod convenience. One failing feed is logged and skipped.
- **CLI**: `--feeds`, `--feeds-file`, `--feed-concurrency`, `--schema`,
  `--schema-file`, `--strict-schema` and `--list-schemas`.
- **Parser**: `is_xml()` detection and XML (`lxml`-backed) parsing of
  RSS/Atom feeds so element text and namespaced tags (e.g. `dc:creator`)
  survive.
- **Transforms**: `clean_text`, `parse_datetime`, `to_float`, `to_int`,
  `to_bool`, `truncate`.
- **ld+json extraction** (`supescrapper.schema`): `extract_ld_json()` extracts
  all Schema.org `NewsArticle` / `Article` / `BlogPosting` objects from
  `<script type="application/ld+json">` tags; `from_ldjson("field.path")`
  transform factory for use in `Field(transform=from_ldjson("author.name"))`.
- **News article CLI mode** (`--news`): extracts articles from a website URL
  via auto-discovered RSS feeds + section-page scanning, then fetches article
  pages and extracts with ld+json (preferred) → `NEWS_SCHEMA` CSS fallback.
  Options: `--target N` (default 20), `--max-pages N` (default 5),
  `--no-ldjson`. Works with `--export csv|xlsx|json|sqlite`, `--output`,
  `--table`.
- **Large-scale news crawl mode** (`--news --crawl`, `supescrapper.news_crawler`):
  multi-source crawler aimed at 100k+ unique articles. Works with a single
  positional URL (`--news --crawl https://bbc.com/news`) or `--sources` lists.
  Seed ladder per source: RSS → sitemaps (robots.txt discovery + recursive,
  `.gz`-compressed supported) → BFS link crawling → Google News RSS fallback.
  `--target N` is the crawl goal: per-source caps default to the target split
  across sources, so discovery rounds scale up (sitemap/BFS engage when RSS
  alone can't fill the target). Threaded per-source workers with per-host
  politeness, incremental checkpointing (`--checkpoint`/`--resume`, CSV append),
  and global + cross-host path dedup (www./.com/.co.uk twins of one article
  collapse to a single record). Seeds are filtered to the source's host family
  (registrable domain, e.g. `bbc.com` ≡ `bbc.co.uk`) so syndicated or unrelated
  articles that feeds and Google News queries return never leak into a
  source's records.
- **XLSX export**: `--export xlsx` (or `xls`) writes `.xlsx` files via
  `openpyxl`; new `export_xlsx()` and `export_records()` in `supescrapper.exporters`.
- **Per-site schema configs** (`examples/site_configs/`): JSON schema
  definitions (e.g. `bloomberg.json` with `dc:creator`, `guid@isPermaLink`)
  auto-loaded via `examples/site_configs/registry.load_all()` for use in
  batch RSS collectors.
- **Batch RSS collector** (`examples/batch_rss_collect.py`): production-ready
  10-source × 100-article collector using primary RSS + Google News
  `site:domain.com when:1d/7d/30d/1y` fallback; live console progress with
  stuck detection; error isolation per source.
- **Demo website scraper** (`examples/batch_website_ldjson.py`): demonstrates
  static → Playwright fallback and ld+json → CSS fallback for article
  extraction from website homepages.

### Changed

- `ScrapedResultSet.export()` / exporters also accept plain dict record lists.
- `--export` choices expanded: `json`, `csv`, `sqlite`, `xlsx`, `xls`.

### Fixed

- `--news` mode article fetches now share one keep-alive HTTP session with a
  small per-request delay, and the seed URL is resolved to the site's canonical
  host before scanning (e.g. `bbc.com` -> `www.bbc.com` redirects no longer add
  a round-trip to every article fetch). Failed article pages are reported on
  the console instead of being silently dropped.
- News crawl: Google News redirect resolver only followed http/https absolute
  URLs (relative redirects crashed resolution); URL dedup keys now strip `www.`
  so host aliases count as one article; `.gz` sitemaps are fetched and
  decompressed instead of skipped.


Hardening release: correctness fixes for Levels 1–2, shared sync/async core,
and several breaking-behavior corrections.

### Fixed

- **XPath selectors returned wrong elements.** `select_xpath()` discarded the
  lxml matches and re-looked-up tags by name, so `//div[@class='b']//h1` returned
  the first `<h1>` on the page and duplicates were produced. Matched nodes are
  now serialized back to their actual elements with attributes preserved.
- **`find_elements()` / `find_interactive()` returned the `Scraper` instance**
  instead of a `ScrapedResultSet`, breaking iteration and crashing the CLI
  (`--find`, `--find-interactive`).
- **`fetch(user_agent=..., dynamic=True)` raised `TypeError`** (duplicate
  keyword argument).
- **`is_spa()` massive false positives.** Any page whose HTML merely mentioned
  `"react"`, `"next"` or `"vite"` was classified as a SPA, forcing needless
  Playwright launches. Detection is now structure-based (empty framework roots,
  framework data blobs only on content-less pages, hashed build bundles).
- **`extract()` field-spec grammar.** `price="span.price"` used to look up an
  HTML attribute named `price` (returning empty); it now defaults to `.text`.
  Dot-containing CSS selectors like `.product h2` no longer crash.
- **`ScrapedItem.to_dict()` was spoofable**: attributes/meta could overwrite
  `tag`, `text` and `url`. Core fields now always win.
- **SQLite export crashed on hostile attribute names** and used unescaped
  identifiers. Column names are now quoted/escaped and table names validated.
- **`_normalize_url()` broke `host:port` URLs** (`example.com:8080` was parsed
  as scheme `example.com`). Scheme detection now requires `://`.
- **Playwright browser leaked on navigation errors** in the sync fetcher;
  the browser is now always closed.
- CLI no longer prints raw tracebacks for `SupescrapperError` — it prints a
  clean message and exits non-zero.

### Added

- **Shared `ScrapeEngine`** — sync and async scrapers now share one DOM/query
  engine, eliminating divergence between the two implementations.
- **Playwright scroll/wait options** on `fetch()` (both sync and async):
  `scroll=N`, `scroll_delay_ms`, `wait_for=<css>`, `wait_ms`.
- **Session reuse** across `fetch()` calls on `Scraper`, plus `close()` and
  context-manager support (`with Scraper() as s:`).
- **`AsyncScraper` async context manager** (`async with AsyncScraper() as s:`),
  matching the documented example.
- CLI flags `--scroll`, `--wait-for`, `--wait-ms`.
- Library logging via the `supescrapper` logger (no more `print()` to stdout).
- Async test suite and sync/async parity tests.

### Changed (breaking)

- `find_elements()` / `find_interactive()` now return `ScrapedResultSet`.
- `ScrapedItem.to_dict()`: core fields (`tag`/`text`/`url`) can no longer be
  overridden by attributes or meta.
- `extract_fields()` grammar: bare specs default to `.text`; explicit properties
  must be one of `text`, `html`, `href`, `src` (or use `selector@attribute`).
- `get_classifier()` returns a fresh instance; custom rules no longer leak
  across `Scraper`/`AsyncScraper` instances.
- `AsyncScraper` default `wait_until` changed from `networkidle` to
  `domcontentloaded` to match the sync scraper.
- CLI `--find`/`--find-interactive` paths now honour `--where-*` and `--limit`.

## [0.4.0] - 2026-10-21

Scaling release: Level 3 (TLS impersonation / anti-bot) and Level 4
(concurrent crawl engine).

### Added

- **Level 3 — `impersonate` backend** (`supescrapper.impersonate`):
  `ImpersonatingFetcher` / `AsyncImpersonatingFetcher` use [`curl_cffi`](https://github.com/lexiforest/curl_cffi)
  to impersonate real browser TLS/JA3 fingerprints. `ProxyPool` supports
  round-robin / random proxy rotation. `status_is_retryable()` decides whether a
  failed request is worth retrying.
- `Scraper.fetch(..., impersonate=...)` and `AsyncScraper.fetch(..., impersonate=...)`
  accept `True` (uses `chrome124`) or an explicit target
  (see `KNOWN_IMPERSONATE_TARGETS`), plus `proxy_pool` / `proxy_strategy`.
- **Level 4 — `crawler` engine** (`supescrapper.crawler`): `CrawlEngine`
  (thread pool) and `AsyncCrawlEngine` (asyncio) with link extraction, URL
  de-duplication, depth limiting, domain allow-lists, regex allow/deny patterns,
  per-host throttling with optional autothrottle, retry middleware, pluggable
  item pipelines, and CSS-selector pagination (`follow_selector`).
- CLI: `--crawl`, `--concurrency`, `--max-urls`, `--max-depth`,
  `--download-delay`, `--autothrottle`, `--allow-domains`, `--deny-patterns`,
  `--allow-patterns`, `--follow-selector`, `--impersonate`, `--proxy-pool`.
- Optional dependency extras: `impersonate` (`curl_cffi`) and `all`.

### Notes

- `curl_cffi` is optional; importing `supescrapper` does not require it. Calling
  an impersonation fetch without it raises `ImpersonateNotInstalledError` with
  install instructions.

## [0.2.0]

- Initial public API: `Scraper`, `AsyncScraper`, classifiers, exporters, CLI.
