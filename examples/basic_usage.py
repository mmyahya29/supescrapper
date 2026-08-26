from supescrapper import Scraper

# Basic: get all h1 tags
print("=== Basic Fetch ===")
results = Scraper().fetch("https://httpbin.org/html").query("h1").extract()
print(f"Found {len(results)} h1 tags")
for item in results:
    print(f"  - {item.text}")

# Extract links
print("\n=== Links ===")
links = Scraper().fetch("https://httpbin.org/links/50/0").extract_links()
print(f"Found {len(links)} links")

# Extract with named fields
print("\n=== Named Fields ===")
results = Scraper().fetch("https://httpbin.org/html").query("*").where_text("contains", "Herman").extract(
    tag_text="p.text",
)
for item in results:
    print(f"  - {item.to_dict()}")

# Filter by text
print("\n=== Filtered Results ===")
results = (
    Scraper()
    .fetch("https://httpbin.org/links/10/0")
    .query("a")
    .where_attr("href", "contains", "/5")
    .extract()
)
print(f"Found {len(results)} links with /5 in href")
for item in results:
    print(f"  - href={item.attrs.get('href', '')}")

# Export to JSON
print("\n=== Export ===")
results = Scraper().fetch("https://httpbin.org/links/10/0").query("a").extract()
path = results.export("json", "test_output.json")
print(f"Exported to: {path}")
