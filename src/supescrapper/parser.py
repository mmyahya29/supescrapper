from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .models import ScrapedItem
from .exceptions import SelectorError

_PARSER = "lxml"

_SPA_ROOT_IDS = (
    "root", "app", "__next", "__nuxt", "__svelte", "__qwik",
    "svelte", "vue-app", "react-root", "nuxt-root", "app-shell",
)
_SPA_ROOT_TAGS = ("app-root", "nuxt-root", "outlet")
_CONTENT_TAGS = ("img", "picture", "video", "source", "canvas", "a", "iframe")


def _has_content_media(body: Tag) -> bool:
    """True if the body contains any non-text content (media/links/embeds)."""
    if body.find(_CONTENT_TAGS) is not None:
        return True
    return bool(body.select("a[href]"))
_KNOWN_FIELD_PROPS = ("text", "html", "href", "src")


def is_xml(content: str) -> bool:
    """True if content looks like XML / RSS / Atom rather than HTML."""
    if not content:
        return False
    s = content.lstrip()
    if s.startswith("<?xml"):
        return True
    prefix = s[:500].lower()
    return bool(re.search(r"<(?:rss|feed|rdf:rdf|channel)[\s>]", prefix))


def parse(html: str, parser: str | None = None) -> BeautifulSoup:
    if parser is None:
        parser = "xml" if is_xml(html) else _PARSER
    return BeautifulSoup(html, parser)


def is_spa(html: str) -> bool:
    """Heuristic: does this HTML look like an un-rendered JS SPA shell?"""
    # XML, RSS, and Atom feeds are structured data, not SPAs
    if is_xml(html):
        return False

    # Empty or near-empty responses need dynamic rendering
    if len(html.strip()) < 50:
        return True

    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    if body is None:
        return True
    text_content = body.get_text(strip=True)

    # 1. Empty (or spinner-only) root containers used by SPA frameworks.
    #    Whitespace/comment children don't count as rendered content.
    for root_id in _SPA_ROOT_IDS:
        root_el = soup.find(id=root_id)
        if root_el is None or root_el.get_text(strip=True):
            continue
        if len(root_el.find_all(True)) <= 2:
            return True
    for root_tag in _SPA_ROOT_TAGS:
        root_el = soup.find(root_tag)
        if root_el is None or root_el.get_text(strip=True):
            continue
        if len(root_el.find_all(True)) <= 2:
            return True

    # 2. React shell marker on a content-less body
    if soup.find(attrs={"data-reactroot": True}) is not None and len(text_content) < 100:
        return True

    # 3. Framework data blobs only matter when the page has no visible text
    #    (fully SSG/SSR pages carry the same blobs plus real content).
    if len(text_content) < 300:
        if soup.find("script", id="__NEXT_DATA__") is not None:
            return True
        if soup.find("script", id="__NUXT_DATA__") is not None:
            return True
        for s in soup.find_all("script"):
            if not s.get("src") and "window.__NUXT__" in (s.string or ""):
                return True

    # 4. Very little visible text and no non-text content (links/media) → shell
    if len(html) > 500 and len(text_content) < 100 and not _has_content_media(body):
        return True

    # 5. Few real content elements AND almost no text (avoid false positives on simple pages)
    real_tags = [t for t in body.find_all(True) if t.name not in ("script", "style", "meta", "link", "noscript")]
    if len(html) > 500 and len(real_tags) < 5 and len(text_content) < 100 and not _has_content_media(body):
        return True

    # 6. Build-tool bundle scripts (Vite /assets/, Webpack content-hash filenames)
    for s in soup.find_all("script", src=True):
        src = s.get("src", "") or ""
        if re.search(r"/assets/.+\.(?:js|mjs)$", src):
            return True
        if re.search(r"\.[A-Za-z0-9_-]{8,}\.(?:js|mjs)$", src):
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
            if base_url and not val.startswith(("http://", "https://", "mailto:", "tel:", "#", "javascript:")):
                return urljoin(base_url, val)
            return val
    return ""


def select(root: BeautifulSoup | Tag, selector: str) -> list[Tag]:
    """CSS selector → list of Tag."""
    found = root.select(selector)
    if not found:
        raise SelectorError(f"CSS selector '{selector}' matched nothing.")
    return found


def select_xpath(root: BeautifulSoup | Tag, xpath_expr: str) -> list[Tag]:
    """XPath expression → list of Tag (via lxml).

    Each matched lxml node is serialized back to an HTML fragment and
    parsed into an independent BeautifulSoup Tag, so attributes, text and
    nesting of the *actual* matched elements are preserved.
    """
    from lxml import etree

    if isinstance(root, (BeautifulSoup, Tag)):
        html_str = str(root)
    else:
        raise SelectorError("XPath requires a BeautifulSoup or Tag object.")

    try:
        lxml_tree = etree.HTML(html_str)
    except Exception as exc:
        raise SelectorError(f"Failed to parse HTML for XPath: {exc}") from exc

    if lxml_tree is None:
        raise SelectorError(f"XPath '{xpath_expr}' matched nothing.")

    try:
        found = lxml_tree.xpath(xpath_expr)
    except Exception as exc:
        raise SelectorError(f"Invalid XPath '{xpath_expr}': {exc}") from exc

    if not found:
        raise SelectorError(f"XPath '{xpath_expr}' matched nothing.")

    result: list[Tag] = []
    for node in found:
        if isinstance(node, str):
            continue
        if not hasattr(node, "tag"):
            continue
        try:
            frag = etree.tostring(node, method="html", encoding="unicode")
        except Exception:
            continue
        frag_soup = parse(frag)
        # lxml wraps non-root fragments in <html><body>; unwrap to the real node.
        tag = frag_soup.find(node.tag)
        if isinstance(tag, Tag):
            result.append(tag)

    if not result:
        raise SelectorError(f"XPath '{xpath_expr}' matched nothing.")
    return result


def select_by_regex(root: BeautifulSoup | Tag, pattern: str, tag: str = "*") -> list[Tag]:
    """Find tags whose text matches *pattern*."""
    compiled = re.compile(pattern, re.IGNORECASE)
    search_tag = True if tag == "*" else tag
    return [
        t for t in root.find_all(search_tag)
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


def _split_field_spec(spec: str) -> tuple[str, str]:
    """Split a field spec into (css_selector, property).

    Grammar:
      "selector@attr"                   -> attribute *attr* (any name)
      "selector.text|.html|.href|.src"  -> explicit known property
      "selector"                        -> whole spec is CSS; property = "text"

    Any other dot means the whole spec is a CSS selector (so class selectors
    like "span.price" are not mistaken for a property lookup).
    """
    if "@" in spec:
        css_selector, attr = spec.rsplit("@", 1)
        return css_selector, attr
    if "." in spec:
        css_selector, tail = spec.rsplit(".", 1)
        if tail in _KNOWN_FIELD_PROPS and css_selector:
            return css_selector, tail
    return spec, "text"


def extract_fields(tag: Tag, **field_selectors: str) -> dict[str, str]:
    """
    Extract named fields from a parent tag.

    Usage:
        extract_fields(card, title="h2.text", price="span.price.text", link="a@href")
        extract_fields(card, price="span.price")   # defaults to .text
    Format: "selector.property" (property in text/html/href/src),
            "selector@attribute", or just "selector" (defaults to .text).
    """
    result: dict[str, str] = {}
    for field_name, spec in field_selectors.items():
        css_selector, prop = _split_field_spec(spec)

        try:
            child = tag.select_one(css_selector)
        except Exception:
            result[field_name] = ""
            continue
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
