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
- **CLI** — full command-line interface, no code needed

## Install

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

# Find all buttons
buttons = scraper.find_elements("button")

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
        name="h2.text",
        price="span.price.text",
        link="a@href",
        image="img@src",
    )
)

for item in results:
    print(item["name"], item["price"], item["link"])
```

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
  --timeout                  Request timeout in seconds (default: 30)
  --retries                  Number of retries (default: 3)
  --user-agent               Custom user agent string
  --proxy                    HTTP proxy URL
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

element detection:
  --find TYPE                Find all elements of a specific type
  --find-interactive         Find all interactive elements
  --classify                 Classify all query results
  --list-types               List all supported element types
```

### Supported Element Types

The classifier recognizes 25+ element types:

- **Buttons**: button, submit_button, toggle_button, icon_button, hamburger_menu
- **Inputs**: text_input, email_input, password_input, search_input, number_input, date_input, tel_input, url_input, file_input, range_slider
- **Form elements**: textarea, select_dropdown, checkbox, radio_button, form
- **Layout**: header, footer, sidebar, card, modal, accordion, carousel
- **Navigation**: nav, breadcrumb, tabs, pagination
- **Media**: image, avatar, logo, icon, video
- **Interactive**: login_form, signup_form, social_auth_button, add_to_cart, price_display, search_bar, share_button

## License

MIT
