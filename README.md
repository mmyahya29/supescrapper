# supescrapper

Universal web DOM scraper for Python. Fetch any page, query with CSS/XPath, full-text search, filter, extract structured data, export to JSON/CSV/SQLite. Auto-detects SPAs and switches to Playwright when needed.

## Features

- **Auto-detection** — static pages use `requests`, JS-rendered SPAs automatically switch to Playwright
- **Element classification** — detect buttons, inputs, forms, search bars, login forms, and 25+ element types
- **Fluent API** — chain `.fetch().query().where().extract().export()` in one line
- **CSS / XPath / Regex selectors** — query the DOM however you want
- **Full-text search** — find any word anywhere on the page
- **Filter engine** — chain `.where_text()`, `.where_attr()`, `.where_tag()` filters
- **Named field extraction** — pull structured data with `extract(title="h2.text", price="span.price")`
- **Export** — JSON, CSV, SQLite with one call
- **Async support** — `AsyncScraper` for async/await workflows
- **Browser impersonation** — defeat TLS/JA3 fingerprinting with `curl_cffi`
- **Concurrent crawling** — `CrawlEngine` with de-duplication, throttling, and pipelines
- **CLI** — full command-line interface, no code needed

## Install

### Quick Install (PyPI)

```bash
pip install supescrapper
```

For JavaScript-rendered pages (React, Vue, Next.js, etc.):

```bash
pip install supescrapper[dynamic]
playwright install chromium
```

For async support:

```bash
pip install supescrapper[async]
```

For anti-bot TLS impersonation:

```bash
pip install supescrapper[impersonate]
```

Install everything at once:

```bash
pip install supescrapper[all]
playwright install chromium
```

### Development Setup (from source)

Clone and run the automated setup script:

```bash
git clone https://github.com/yourusername/supescrapper.git
cd supescrapper
python setup.py
```

The setup script will:
1. Create `.venv/` virtual environment
2. Install package in editable mode with all extras (`[all]`)
3. Install Playwright Chromium
4. Install `openpyxl` for XLSX export

Or manually:

```bash
# Create venv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install in editable mode with all extras
pip install -e ".[all]"

# Install Playwright browser
playwright install chromium

# For XLSX export
pip install openpyxl
```

### .gitignore

The repository includes a `.gitignore` that excludes:
- Virtual environments (`.venv/`, `venv/`, `env/`)
- Python cache (`__pycache__/`, `*.pyc`)
- IDE folders (`.vscode/`, `.idea/`)
- OS files (`.DS_Store`, `Thumbs.db`)
- Test artifacts (`.pytest_cache/`, `.coverage`)
- Playwright browser binaries
- Environment files (`.env`, `*.key`)

## Quick Start

```python
from supescrapper import Scraper

# Get all headings from a page
results = Scraper().fetch("https://example.com").query("h1").extract()

for item in results:
    print(item.text)
```

## Python API

### Basic Fetching

```python
from supescrapper import Scraper

# Fetch any URL — auto-detects if it needs Playwright
results = Scraper().fetch("https://example.com").query("h1").extract()

# URLs without scheme work too
results = Scraper().fetch("example.com").query("h1").extract()

# Force static only (requests, no browser)
results = Scraper().fetch("https://example.com", dynamic=False).query("h1").extract()

# Force Playwright (for SPAs)
results = Scraper().fetch("https://myapp.com", dynamic=True).query(".card").extract()
```

### Querying with CSS Selectors

```python
results = Scraper().fetch("https://example.com").query("h1").extract()
results = Scraper().fetch("https://example.com").query(".product-card").extract()
results = Scraper().fetch("https://example.com").query("div.container > h2").extract()
results = Scraper().fetch("https://example.com").query("a[href*='product']").extract()
```

### Querying with XPath

```python
results = Scraper().fetch("https://example.com").query("//h1", mode="xpath").extract()
results = Scraper().fetch("https://example.com").query("//div[@class='card']//a", mode="xpath").extract()
```

### Querying with Regex

```python
# Find all elements whose text matches a regex pattern
results = Scraper().fetch("https://example.com").query(r"\$\d+\.\d{2}", mode="regex").extract()
```

### Full-Text Search

```python
# Find every element on the page containing the word "price"
results = Scraper().fetch("https://example.com").search("price").extract()

# Case-sensitive search
results = Scraper().fetch("https://example.com").search("JavaScript", case_sensitive=True).extract()
```

### Element Detection

```python
from supescrapper import Scraper

scraper = Scraper()
scraper.fetch("https://example.com")

# Find all buttons (returns a ScrapedResultSet — iterable, sized)
buttons = scraper.find_elements("button")
print(len(buttons))
for item in buttons:
    print(item.text, item.classification)

# Find all search inputs
search_fields = scraper.find_elements("search_input")

# Find all interactive elements (buttons, inputs, links, etc.)
interactive = scraper.find_interactive()

# Find login forms
login_forms = scraper.find_elements("login_form")

# Classify all elements on a page
results = scraper.query("form").classify()

# Check classifications
for item in results:
    print(item.classification)  # e.g. ['form', 'login_form']
```

### Filtering Results

```python
results = (
    Scraper()
    .fetch("https://example.com/products")
    .query(".product-card")
    .where_text("contains", "Sale")
    .extract()
)

# Filter by attribute
results = (
    Scraper()
    .fetch("https://example.com")
    .query("a")
    .where_attr("href", "contains", "/blog")
    .extract()
)

# Filter by tag name
results = (
    Scraper()
    .fetch("https://example.com")
    .query("div")
    .where_tag("section")
    .extract()
)

# Chain multiple filters
results = (
    Scraper()
    .fetch("https://example.com/products")
    .query(".product")
    .where_text("contains", "Wireless")
    .where_attr("data-price", "matches", r"\d+")
    .limit(10)
    .extract()
)

# Custom predicate function
results = (
    Scraper()
    .fetch("https://example.com/products")
    .query(".product")
    .where(lambda item: len(item.text) > 50)
    .extract()
)
```

### Named Field Extraction

```python
# Extract structured data from each matched element
results = (
    Scraper()
    .fetch("https://example.com/products")
    .query(".product-card")
    .extract(
        name="h2.text",        # explicit .text
        price="span.price",    # bare selector -> defaults to .text
        link="a@href",         # attribute via @
        image="img@src",
    )
)

for item in results:
    print(item["name"], item["price"], item["link"])
```

**Field spec grammar:** `selector.property` where `property` is one of
`text`, `html`, `href`, `src`; `selector@attribute` for any attribute; or just
`selector` (defaults to `.text`). Dot-containing CSS selectors such as
`.product h2` are treated as selectors, not properties.

### Extract Links and Images

```python
# All links on a page
links = Scraper().fetch("https://example.com").extract_links()
for link in links:
    print(link.text, "->", link.url)

# All images
images = Scraper().fetch("https://example.com").extract_images()
for img in images:
    print(img.attrs.get("src"))
```

### Extract Tables

```python
scraper = Scraper().fetch("https://example.com/data")
table_data = scraper.extract_table("table")

for row in table_data:
    print(row)
# {'Name': 'Alice', 'Age': '30', 'City': 'NYC'}
# {'Name': 'Bob', 'Age': '25', 'City': 'LA'}
```

### Parse Local HTML

```python
from supescrapper import Scraper

html = open("page.html").read()
results = Scraper().from_html(html).query("h1").extract()
```

### Working with Results

```python
results = Scraper().fetch("https://example.com").query("a").extract()

# Iterate
for item in results:
    print(item.tag, item.text, item.url, item.attrs)

# Access by index
first = results[0]
last = results[-1]

# Get first/last
first = results.first()
last = results.last()

# Length
print(len(results))

# Convert to list of dicts
data = results.to_list()

# Convert to JSON string
json_str = results.to_json(indent=2)

# Get dict from a single item
item = results[0]
d = item.to_dict()  # {'tag': 'a', 'text': 'Click here', 'href': '/page'}
```

### Exporting

```python
results = Scraper().fetch("https://example.com").query("a").extract()

# Export to JSON
results.export("json", "links.json")

# Export to CSV
results.export("csv", "links.csv")

# Export to SQLite (with deduplication)
results.export("sqlite", "links.db", table="links", unique_keys=["url"])
```

### Async Scraper

```python
import asyncio
from supescrapper import AsyncScraper

async def main():
    async with AsyncScraper() as scraper:
        await scraper.fetch("https://example.com")
        results = scraper.query("h1").extract()
        for item in results:
            print(item.text)

asyncio.run(main())
```

> Note: `fetch()` must be awaited before chaining DOM calls:
> `await scraper.fetch(url)` then `scraper.query(...)`.

### Browser Impersonation (anti-bot)

Some sites block plain `requests` traffic by fingerprinting the TLS/JA3
handshake. With the `impersonate` extra installed, `fetch()` can present a real
browser fingerprint instead.

```bash
pip install "supescrapper[impersonate]"
```

```python
# True -> uses the default target (chrome124)
results = Scraper().fetch("https://example.com", impersonate=True).query("h1").extract()

# Or pick an explicit target
results = Scraper().fetch("https://example.com", impersonate="chrome110").query("h1").extract()

# Rotate through a proxy pool
results = (
    Scraper()
    .fetch(
        "https://example.com",
        impersonate=True,
        proxy_pool=["http://p1:8080", "http://p2:8080"],
        proxy_strategy="round_robin",  # or "random"
    )
    .query("h1")
    .extract()
)
```

If `curl_cffi` is not installed, impersonation calls raise
`ImpersonateNotInstalledError` with install instructions. Everything else in
the library keeps working without it. See `KNOWN_IMPERSONATE_TARGETS` for the
full list of supported targets.

### Crawling (Level 4)

`CrawlEngine` follows links across a site with a bounded thread pool, URL
de-duplication, domain filtering, throttling, retries and item pipelines.

```python
from supescrapper import CrawlConfig, CrawlEngine
from supescrapper.models import ScrapedItem

def build_items(soup):
    return [ScrapedItem(tag="a", text=a.get_text(strip=True)) for a in soup.select("a")]

config = CrawlConfig(
    max_urls=500,
    concurrency=8,
    max_depth=3,
    allowed_domains=["example.com"],
    deny_patterns=[r"/logout", r"\.pdf$"],
    download_delay=0.5,          # seconds between requests to the same host
    autothrottle=True,           # adapt the delay to server responses
    follow_selector="a.next",    # follow "next page" links (pagination)
)

engine = CrawlEngine(config)
for result in engine.crawl("https://example.com", item_builder=build_items):
    print(result.url, result.status, len(result.items))
```

An async variant mirrors the same config:

```python
import asyncio
from supescrapper import AsyncCrawlEngine, CrawlConfig

async def main():
    engine = AsyncCrawlEngine(CrawlConfig(max_urls=500, concurrency=16))
    results = await engine.crawl("https://example.com")
    print(len(results), "URLs crawled")

asyncio.run(main())
```

`CrawlResult` exposes `url`, `status` (`"ok"` / `"error"`), `items`, `error`
and `depth`. Pipelines are plain callables `item -> item | None`; return `None`
to drop an item.

### Proxy and Headers

```python
results = (
    Scraper()
    .fetch(
        "https://example.com",
        proxies={"http": "http://proxy:8080", "https": "http://proxy:8080"},
        headers={"Authorization": "Bearer token123"},
        cookies={"session": "abc123"},
        user_agent="MyBot/1.0",
        timeout=60,
    )
    .query("h1")
    .extract()
)
```

### Dynamic Page Options (Playwright)

```python
# Scroll to trigger lazy loading, then wait for a selector
results = (
    Scraper()
    .fetch(
        "https://myapp.com",
        dynamic=True,
        wait_until="networkidle",
        scroll=3,               # wheel down 3 times
        wait_for=".card",       # wait for this CSS selector
        wait_ms=500,            # extra fixed wait (ms)
        viewport={"width": 1440, "height": 900},
    )
    .query(".card")
    .extract()
)
```

### Session Reuse & Cleanup

```python
# The HTTP session is reused across fetch() calls on the same instance
with Scraper() as scraper:
    scraper.fetch("https://example.com/1")
    scraper.fetch("https://example.com/2")   # same session, keep-alive
```

### Error Handling

```python
from supescrapper import Scraper, FetchError, SelectorError

try:
    results = Scraper().fetch("https://bad-url.xyz").query("h1").extract()
except FetchError as e:
    print(f"Failed to fetch: {e}")
except SelectorError as e:
    print(f"Selector matched nothing: {e}")
```

## CLI Usage

### Basic

```bash
# Get all h1 tags
supescrapper https://example.com --select "h1" --extract text

# Get all links
supescrapper https://example.com --extract-links --table

# Full-text search
supescrapper https://example.com --search "price" --table
```

### Element Detection

```bash
# Find all buttons
supescrapper https://example.com --find button

# Find all search inputs
supescrapper https://example.com --find search_input

# Find all interactive elements
supescrapper https://example.com --find-interactive

# Find login forms
supescrapper https://example.com --find login_form

# Classify all elements
supescrapper https://example.com --select "form" --classify

# List all supported element types
supescrapper --list-types
```

### Filtering

```bash
# Filter by text content
supescrapper https://example.com --select "a" --where-text contains "blog" --table

# Filter by attribute
supescrapper https://example.com --select "a" --where-attr href contains "/product" --table

# Limit results
supescrapper https://example.com --select "div.item" --limit 5 --raw
```

### Named Fields

```bash
supescrapper https://example.com/products \
  --select ".product-card" \
  --extract title="h2.text" price="span.price.text" link="a@href" \
  --export json -o products.json
```

### Export

```bash
# JSON
supescrapper https://example.com --select "a" --export json -o links.json

# CSV
supescrapper https://example.com --select "a" --export csv -o links.csv

# SQLite
supescrapper https://example.com --select "a" --export sqlite -o links.db
```

### Dynamic Pages (SPA)

```bash
# Auto-detect (default behavior)
supescrapper https://myapp.com --select ".card" --table

# Force Playwright
supescrapper https://myapp.com --select ".card" --dynamic --table

# Force static only
supescrapper https://example.com --select "h1" --static --table

# Change wait condition
supescrapper https://myapp.com --select ".card" --dynamic --wait-until networkidle --table
```

### Browser Impersonation

```bash
# Impersonate a browser TLS fingerprint (default target: chrome124)
supescrapper https://example.com --select "h1" --impersonate

# Explicit target
supescrapper https://example.com --select "h1" --impersonate chrome110

# Rotate proxies
supescrapper https://example.com --select "h1" --impersonate \
  --proxy-pool http://p1:8080 http://p2:8080
```

### Crawling a Site

```bash
# Crawl following links, selecting the same thing on each page
supescrapper https://example.com --crawl --select "h2" --max-urls 200 --table

# Restrict depth/domain, throttle, and export matches
supescrapper https://example.com --crawl --select "a.product" \
  --max-depth 3 --allow-domains example.com --download-delay 0.5 \
  --export csv -o products.csv

# Follow a "next page" pagination link
supescrapper https://example.com/list --crawl --select ".item" --follow-selector "a.next"
```

### News Articles from Website (`--news`)

Extract news articles from any news website URL. The scraper auto-discovers RSS feeds, scans section pages for article links, fetches each article, and extracts structured data using **ld+json (Schema.org NewsArticle)** as the primary method with **CSS fallback** (`NEWS_SCHEMA`).

```bash
# Extract and display as table
supescrapper https://bbc.com/news --news --target 20 --table

# Export to CSV
supescrapper https://bbc.com/news --news --target 20 --export csv -o articles.csv

# Export to XLSX (Excel)
supescrapper https://bbc.com/news --news --target 20 --export xlsx -o articles.xlsx

# Export to JSON
supescrapper https://bbc.com/news --news --target 20 --export json -o articles.json

# Use CSS extraction only (skip ld+json)
supescrapper https://cnn.com --news --target 10 --no-ldjson --table

# Increase section pages scanned (default: 5)
supescrapper https://reuters.com --news --target 50 --max-pages 10 --export csv -o reuters.csv
```

**How it works:**
1. **RSS Discovery** — finds feeds via `<link rel="alternate">` and common paths (`/rss`, `/feed`, `/rss.xml`)
2. **RSS Extraction** — uses `rss` schema to pull article URLs from discovered feeds
3. **Website Scanning** — if RSS yields fewer than target, scans homepage + section pages (`/news`, `/world`, `/latest`, etc.) for article links
4. **Article Fetching** — concurrent fetches of each article page (static HTTP)
5. **Extraction** — ld+json (Schema.org `NewsArticle`) preferred → CSS fallback (`NEWS_SCHEMA`)
6. **Full Article Text** — scrapes article body from common containers (`article`, `[itemprop='articleBody']`, `.article-body`, `.post-content`, `.entry-content`, `main`, etc.) and returns **up to 1000 words**
7. **Author Name Resolution** — handles string, Schema.org Person object (`{"@type": "Person", "name": "John"}`), or author arrays
8. **Output** — `--table` (terminal), `--export csv|xlsx|json|sqlite`, `--output` file

**Options:**
| Flag | Description |
|------|-------------|
| `--news` | Enable news article extraction mode |
| `--target N` | Target number of articles (default: 20) |
| `--max-pages N` | Max section pages to scan for links (default: 5) |
| `--no-ldjson` | Skip ld+json, use CSS only |
| `--export FMT` | `json`, `csv`, `sqlite`, `xlsx`, `xls` |
| `--output PATH` | Output file (required with `--export`) |
| `--table` | Print as terminal table |

For reliable high-volume news collection (100s of articles per source), use the batch RSS collector instead: `examples/batch_rss_collect.py`.

For reliable high-volume news collection (100s of articles per source), use the batch RSS collector instead: `examples/batch_rss_collect.py`.

### Full CLI Reference

```bash
supescrapper --help
```

```
positional arguments:
  url                        URL to scrape

options:
  --html-file, -f            Local HTML file to parse
  --select, -s               CSS selector
  --select-xpath             XPath selector
  --select-regex             Regex to match element text
  --search                   Full-text search for a term
  --extract, -e              Named fields, e.g. title="h2.text"
  --dynamic, -d              Force Playwright
  --static                   Force static requests only
  --wait-until               Playwright wait condition (default: domcontentloaded)
  --scroll N                 Wheel-scroll down N times (Playwright, lazy loading)
  --wait-for SELECTOR        Wait for a CSS selector before capture (Playwright)
  --wait-ms N                Fixed wait in milliseconds after navigation (Playwright)
  --timeout                  Request timeout in seconds (default: 30)
  --retries                  Number of retries (default: 3)
  --user-agent               Custom user agent string
  --proxy                    HTTP proxy URL
  --impersonate [TARGET]     curl_cffi TLS impersonation (default: chrome124)
  --proxy-pool URL [URL...]  Rotating proxy pool
  --where-text OP VALUE      Filter: "contains|equals|matches" value
  --where-attr ATTR OP VALUE Filter by attribute
  --where-tag TAG            Filter by tag name
  --limit, -n N              Limit results
  --export {json,csv,sqlite} Export format
  --output, -o PATH          Output file path
  --table                    Print as terminal table
  --raw                      Print raw JSON
  --extract-links            Extract all links
  --extract-images           Extract all images
  --extract-table            Extract HTML table

crawl:
  --crawl                    Crawl from URL, following links
  --concurrency N            Concurrent requests (default: 8)
  --max-urls N               Maximum URLs to crawl (default: 1000)
  --max-depth N              Maximum link depth from the seed
  --download-delay SECONDS   Delay between requests to the same host
  --autothrottle             Adapt the delay to server responses
  --allow-domains D [D...]   Restrict crawl to these domains
  --deny-patterns RE [RE...] Skip URLs matching these regexes
  --allow-patterns RE [RE...] Only crawl URLs matching these regexes
  --follow-selector CSS      CSS selector for "next page" links (pagination)

element detection:
  --find TYPE                Find all elements of a specific type
  --find-interactive         Find all interactive elements
  --classify                 Classify all query results
  --list-types               List all supported element types
```

### Supported Element Types

The classifier recognizes 40+ element types:

- **Buttons**: button, submit_button, toggle_button, icon_button, hamburger
- **Inputs**: text_input, email_input, password_input, search_input, number_input, date_input, tel_input, url_input, file_input, range_slider
- **Form elements**: textarea, select_dropdown, checkbox, radio, form
- **Layout**: header, footer, sidebar, card, modal, accordion, carousel
- **Navigation**: nav, breadcrumb, tabs, pagination
- **Media**: image, avatar, logo, icon, video
- **Interactive**: login_form, signup_form, social_auth_button, add_to_cart, price_display, search_bar, share_button, social_link

## Schemas and feeds

Schemas let you declare *what* to extract from each record and reuse the
declaration across pages, feeds and crawl runs:

```python
from supescrapper import Scraper, Schema, Field, register_schema

# Register a site-specific schema once...
register_schema(Schema(
    "product",
    item=[".product-card", "li.product"],
    fields={
        "name": "h2",
        "price": Field(".price", transform=float),
        "url": "a@href",
        "tags": Field(".tag", many=True),
    },
))

# ...then apply it anywhere.
data = (
    Scraper()
    .fetch("https://example.com/products")
    .scrape("product")
)
for record in data:
    print(record["name"], record["price"], record["url"])

data.export("json", "products.json")
```

### Built-in schemas

``supescrapper`` ships with ready-made schemas, registered under the names
``news``, ``article``, ``product`` and ``rss`` (aliased as ``feed`` and
``news_feed``):

```python
from supescrapper import Scraper

# RSS / Atom feed items
items = Scraper().fetch("https://example.com/rss").scrape("rss")
# each record: article_id, title, description, source_urls, author_name,
#              date_published, plus scraped_at / time_scraped / source_url

# News cards on a list page
items = Scraper().fetch("https://example.com/news").scrape("news")
```

`rss` handles both RSS 2.0 and Atom feeds. Fields are resolved with forgiving
fallbacks — e.g. ``author_name`` reads `dc:creator`, then `.author`, then
`creator`; ``date_published`` is parsed from RFC 822 / ISO 8601 dates.

### Defining your own schema

```python
from supescrapper import Schema, Field, parse_datetime

news = Schema(
    "my-news",
    item="article, .post",
    fields={
        "title": Field(["h1", ".headline", "h2"]),
        "description": Field([".excerpt", ".summary", "p"]),
        "url": Field(["link[rel='canonical']@href", "a@href"], absolute_url=True),
        "author": Field([".author", ".byline"]),
        "published_at": Field(["time", "[datetime]", ".date"], transform=parse_datetime),
        "tags": Field(".tag", many=True),
    },
)
register_schema(news)

data = Scraper().fetch(url).scrape("my-news")
```

- `item` selects the record root (omit it to treat the whole page as one
  record; if nothing matches, the page is used as a single record).
- Field selectors are tried in order; the first candidate with a value wins.
  A candidate may end with ``@attr`` to read an attribute, e.g.
  ``"link[rel='canonical']@href"``.
- `many=True` collects all matches into a list; `transform` post-processes
  values (built-ins: ``clean_text``, ``parse_datetime``, ``to_float``,
  ``to_int``, ``to_bool``, ``truncate(n)``).
- Records always include `scraped_at` (when scraped), `source_url` (the page
  URL) and `schema` (the schema name) — disable with
  `system_fields=()`.

### Loading schemas from JSON

```python
from supescrapper import load_schema_from_json, register_schema

schema = load_schema_from_json("schemas/product.json")
register_schema(schema)
```

### Scraping many feeds at once

```python
from supescrapper import scrape_feeds, async_scrape_feeds

records = scrape_feeds(
    ["https://example.com/rss", "https://example.com/atom"],
    concurrency=4,
)

# async variant
records = await async_scrape_feeds(
    ["https://example.com/rss", "https://example.com/atom"],
)
```

Every record carries `source_url` (the feed it came from), `scraped_at` and
`time_scraped`. One failing feed is logged and skipped instead of aborting the
batch.

### CLI

```bash
# List registered schemas
supescrapper --list-schemas

# Scrape several RSS/Atom feeds at once and export
supescrapper --feeds https://example.com/rss https://example.com/atom \
  --schema rss --export json -o feed.json

# Or load a schema from JSON
supescrapper --feeds-file feeds.txt --schema-file my-schema.json --table
```

## Batch scraping many sources from a config file

To scrape many news sources at once — for example, 10 sources × 100 articles
each — put the sources and limits in a config file and run it in one command.

### Config file (JSON or TOML)

**JSON** (`scrape.json`):

```json
{
  "scrape": {
    "schema": "news",
    "concurrency": 4,
    "max_articles_per_source": 100,
    "date_from": "2026-10-01",
    "date_to": "2026-10-21",
    "export": "csv",
    "output": "news.csv",
    "sources": [
      { "url": "https://example.com/news", "max_articles": 100 },
      { "url": "https://example.org/feed", "schema": "rss", "max_articles": 100 }
    ]
  }
}
```

**TOML** (`scrape.toml`):

```toml
[scrape]
schema = "news"
concurrency = 4
max_articles_per_source = 100
date_from = "2026-10-01"
date_to = "2026-10-21"
export = "csv"
output = "news.csv"

[[sources]]
url = "https://example.com/news"
max_articles = 100

[[sources]]
url = "https://example.org/feed"
schema = "rss"
max_articles = 100
```

### Run it

Python:

```python
from supescrapper import load_config, run_config

cfg = load_config("scrape.json")
results = run_config(cfg)   # SchemaResultSet — also exported to news.csv
```

CLI:

```bash
supescrapper --config scrape.json
```

### What you get

Each source is scraped concurrently. For every article the output record
contains exactly:

- `source_url` — the source feed/page URL
- `title` — article title
- `description` — article summary/description
- `author` — article author
- `time_published` — publication date (RFC 822 / ISO 8601 normalized)
- `query` — the schema name used to extract it

Only articles whose `time_published` falls inside `date_from`…`date_to` are
kept, and each source stops after `max_articles` records. One failing source
is logged and skipped instead of aborting the whole batch.

Export to CSV, JSON, or SQLite with the `export`/`output` config keys (or
`--export`/`--output` on the CLI).

## License

MIT

