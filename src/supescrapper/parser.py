from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag, NavigableString

from .models import ScrapedItem
from .exceptions import SelectorError

_PARSER = "lxml"

_SPA_ROOT_IDS = ("root", "app", "__next", "__nuxt", "__svelte", "__qwik", "svelte", "vue-app", "react-root")
_SPA_BODY_MARKERS = (
    "react", "vue", "angular", "svelte", "next", "nuxt",
    "vite", "webpack", "__remix", "ember", "solidjs",
)


def parse(html: str, parser: str = _PARSER) -> BeautifulSoup:
    return BeautifulSoup(html, parser)


def is_spa(html: str) -> bool:
    """Heuristic: does this HTML look like an un-rendered JS SPA shell?"""
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    if body is None:
        return False

    # 1. Check for empty root containers
    for root_id in _SPA_ROOT_IDS:
        root_el = soup.find(id=root_id)
        if root_el is not None and len(root_el.get_text(strip=True)) == 0 and len(list(root_el.children)) == 0:
            return True

    # 2. Very little visible text relative to total HTML size
    text_content = body.get_text(strip=True)
    if len(html) > 500 and len(text_content) < 100:
        return True

    # 3. Few real content elements AND almost no text (avoid false positives on simple pages)
    real_tags = [t for t in body.find_all(True) if t.name not in ("script", "style", "meta", "link", "noscript")]
    if len(html) > 500 and len(real_tags) < 5 and len(text_content) < 100:
        return True

    # 4. SPA framework markers in scripts or comments
    html_lower = html.lower()
    for marker in _SPA_BODY_MARKERS:
        if f'"{marker}"' in html_lower or f"'{marker}'" in html_lower:
            return True

    # 5. Module scripts with hashed filenames (Vite, Webpack, etc.)
    scripts = soup.find_all("script", {"type": "module"})
    for s in scripts:
        src = s.get("src", "")
        if re.search(r"/assets/.+\.(js|mjs)$", src):
            return True

    return False


def _tag_text(tag: Tag) -> str:
    return tag.get_text(separator=" ", strip=True)


def _tag_attrs(tag: Tag) -> dict[str, str]:
    return {k: str(v) for k, v in tag.attrs.items()}


def _tag_url(tag: Tag, base_url: str = "") -> str:
    for key in ("href", "src", "data-src", "data-href"):
        val = tag.get(key)
        if val and isinstance(val, str):
            return val
    return ""


def select(root: BeautifulSoup | Tag, selector: str) -> list[Tag]:
    """CSS selector → list of Tag."""
    found = root.select(selector)
    if not found:
        raise SelectorError(f"CSS selector '{selector}' matched nothing.")
    return found


def select_xpath(root: BeautifulSoup | Tag, xpath_expr: str) -> list[Tag]:
    """XPath expression → list of Tag (via lxml)."""
    if hasattr(root, "xpath"):
        found = root.xpath(xpath_expr)
    else:
        raise SelectorError("XPath requires lxml parser.")
    result = [n for n in found if isinstance(n, Tag)]
    if not result:
        raise SelectorError(f"XPath '{xpath_expr}' matched nothing.")
    return result


def select_by_regex(root: BeautifulSoup | Tag, pattern: str, tag: str = "*") -> list[Tag]:
    """Find tags whose text matches *pattern*."""
    compiled = re.compile(pattern, re.IGNORECASE)
    return [
        t for t in root.find_all(tag)
        if isinstance(t, Tag) and compiled.search(_tag_text(t))
    ]


def tags_to_items(tags: list[Tag], base_url: str = "") -> list[ScrapedItem]:
    """Convert a list of BS4 Tags into ScrapedItem instances."""
    items: list[ScrapedItem] = []
    for tag in tags:
        items.append(ScrapedItem(
            tag=tag.name,
            text=_tag_text(tag),
            html=str(tag),
            attrs=_tag_attrs(tag),
            url=_tag_url(tag, base_url),
        ))
    return items


def extract_fields(tag: Tag, **field_selectors: str) -> dict[str, str]:
    """
    Extract named fields from a parent tag.

    Usage:
        extract_fields(card, title="h2.text", price="span.price.text", link="a@href")
    Format: "selector.property" or just "selector" (defaults to .text)
    """
    result: dict[str, str] = {}
    for field_name, spec in field_selectors.items():
        parts = spec.rsplit(".", 1)
        css_selector = parts[0]
        prop = parts[1] if len(parts) == 2 else "text"

        child = tag.select_one(css_selector)
        if child is None:
            result[field_name] = ""
            continue

        if prop == "text":
            result[field_name] = _tag_text(child)
        elif prop == "html":
            result[field_name] = str(child)
        elif prop == "href":
            result[field_name] = child.get("href", "")
        elif prop == "src":
            result[field_name] = child.get("src", "")
        else:
            result[field_name] = str(child.get(prop, ""))
    return result


def extract_table(root: BeautifulSoup | Tag, table_selector: str = "table") -> list[dict[str, str]]:
    """Extract an HTML table into a list of dicts (header row → keys)."""
    table = root.select_one(table_selector)
    if table is None:
        raise SelectorError(f"No table found with selector '{table_selector}'.")

    rows = table.find_all("tr")
    if not rows:
        return []

    headers = [_tag_text(th) for th in rows[0].find_all(["th", "td"])]
    data: list[dict[str, str]] = []
    for row in rows[1:]:
        cells = [_tag_text(td) for td in row.find_all(["td", "th"])]
        data.append(dict(zip(headers, cells)))
    return data


def extract_links(root: BeautifulSoup | Tag, selector: str = "a[href]") -> list[ScrapedItem]:
    tags = root.select(selector)
    return tags_to_items(tags)


def extract_images(root: BeautifulSoup | Tag, selector: str = "img[src]") -> list[ScrapedItem]:
    tags = root.select(selector)
    return tags_to_items(tags)


def search_text(root: BeautifulSoup | Tag, term: str, *, case_sensitive: bool = False) -> list[ScrapedItem]:
    """
    Full-text search: find every element that directly contains *term* in its text nodes.
    Returns the deepest (most specific) matching elements, not ancestors.
    """
    flags = 0 if case_sensitive else re.IGNORECASE

    # Step 1: find NavigableString nodes containing the term (fast, built-in)
    text_nodes = root.find_all(string=re.compile(re.escape(term), flags))
    if not text_nodes:
        return []

    # Step 2: get the parent tag of each text node — that's the deepest element
    seen: set[int] = set()
    items: list[ScrapedItem] = []
    for node in text_nodes:
        parent = node.parent
        if parent is None or not isinstance(parent, Tag):
            continue
        tag_id = id(parent)
        if tag_id not in seen:
            seen.add(tag_id)
            items.append(ScrapedItem(
                tag=parent.name,
                text=_tag_text(parent),
                html=str(parent),
                attrs=_tag_attrs(parent),
                url=_tag_url(parent),
            ))
    return items
