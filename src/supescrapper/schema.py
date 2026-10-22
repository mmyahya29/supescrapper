"""Declarative scraping schemas.

A :class:`Schema` describes what a *record* looks like on a page: which
elements are record roots, and how each named field (title, author,
published_at, ...) is resolved inside them. Schemas are registered by name
so the same extraction rules can be reused across pages, feeds and crawl
runs::

    from supescrapper import Scraper, Field, Schema, register_schema

    job = Schema(
        "job",
        item=[".job-card", "li.job"],
        fields={
            "title": "h2",
            "company": ".company",
            "url": "a@href",
            "salary": Field(".salary", pattern=r"[\\d,]+", transform=float),
            "tags": Field(".tag", many=True),
        },
    )
    register_schema(job)

    data = Scraper().fetch("https://example.com/jobs").scrape("job")
    data.export("json", "jobs.json")

Every record automatically carries the configured ``system_fields``
(``schema``, ``source_url``, ``scraped_at``) so collected data stays
traceable.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .exceptions import SchemaError, SelectorError
from .parser import _split_field_spec, _tag_text, select_by_regex, select_xpath

_log = logging.getLogger("supescrapper")

_MODES = ("css", "xpath", "regex")
_SOURCES = ("auto", "text", "html", "attr", "href", "src", "content")
_SYSTEM_FIELDS = ("schema", "source_url", "scraped_at", "time_scraped")


# --- field transforms ------------------------------------------------------

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%d %b %Y %H:%M", "%d %b %Y", "%b %d, %Y %H:%M", "%b %d, %Y",
    "%B %d, %Y", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y %H:%M",
    "%m/%d/%Y", "%Y/%m/%d",
)


def clean_text(value: str) -> str:
    """Collapse all whitespace runs into single spaces."""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_datetime(value: Any) -> Any:
    """Parse a date/datetime string into ISO 8601; pass through if unparseable."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if not v:
        return value
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    try:
        import email.utils
        dt = email.utils.parsedate_to_datetime(v)
        if dt is not None:
            return dt.isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(v, fmt).isoformat()
        except ValueError:
            continue
    return value


def to_float(value: Any) -> float | None:
    """Extract the first number, handling US/EU separators ('1,299.00', '1.299,00')."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:[.,]\d+)*", str(value))
    if not m:
        return None
    num = m.group()
    if "," in num and "." in num:
        num = num.replace(".", "").replace(",", ".") if num.rfind(",") > num.rfind(".") else num.replace(",", "")
    elif "," in num:
        parts = num.split(",")
        num = num.replace(",", ".") if len(parts) == 2 and len(parts[1]) != 3 else num.replace(",", "")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    """Extract the first integer; None if there is none."""
    f = to_float(value)
    if f is None:
        return None
    return int(f)


_TRUE = {"true", "yes", "y", "1", "on", "in stock", "instock", "available"}
_FALSE = {"false", "no", "n", "0", "off", "out of stock", "outofstock", "sold out", "unavailable"}


def to_bool(value: Any) -> bool | None:
    """Map common truthy/falsey strings to bool; None when unknown."""
    if isinstance(value, bool):
        return value
    key = clean_text(value).lower() if value is not None else ""
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    return None


def truncate(length: int) -> Callable[[Any], str]:
    """Transform factory: cut the value to *length* characters."""
    return lambda v: str(v)[:length]


def extract_ld_json(soup: BeautifulSoup | Tag) -> list[dict[str, Any]]:
    """Extract all Schema.org NewsArticle/Article objects from ``<script type="application/ld+json">`` tags.

    Returns a list of dicts (one per matched object). Handles both single objects
    and ``@graph`` arrays.
    """
    if not hasattr(soup, "find_all"):
        return []
    results: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@graph"):
                items.extend(item["@graph"])
        for item in items:
            if not isinstance(item, dict):
                continue
            type_ = item.get("@type")
            if isinstance(type_, list):
                types = type_
            else:
                types = [type_] if type_ else []
            if any(t in ("NewsArticle", "Article", "BlogPosting", "Report") for t in types):
                results.append(item)
    return results


def from_ldjson(field: str, *, first: bool = True) -> Callable[[BeautifulSoup | Tag], Any]:
    """Transform factory: extract a field from page-level ld+json.

    Usage:
        Field("title", transform=from_ldjson("headline"))
        # or for author name:
        Field("author", transform=from_ldjson("author.name"))

    Nested fields use dot notation (e.g. ``"author.name"``, ``"publisher.name"``).
    If multiple ld+json objects match, ``first=True`` returns the first, ``first=False``
    returns a list of all values.
    """
    def _get_nested(obj: dict, path: str) -> Any:
        cur: Any = obj
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit():
                idx = int(part)
                cur = cur[idx] if 0 <= idx < len(cur) else None
            else:
                return None
        return cur

    def transform(soup: BeautifulSoup | Tag) -> Any:
        ld_items = extract_ld_json(soup)
        if not ld_items:
            return None
        vals = [_get_nested(item, field) for item in ld_items]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return vals[0] if first else vals

    return transform


# --- field / schema definitions --------------------------------------------

def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value) if value is not None else ""


@dataclass
class Field:
    """How to resolve one named value inside a record root.

    selector:
        CSS/XPath/regex selector (or an ordered list of candidates — the
        first candidate that yields a value wins). Empty string = the root
        element itself.
    source:
        ``auto`` (default) picks ``content``/``datetime``/``value``
        attributes, then text, then ``src``/``href`` — which handles meta,
        ``<time>`` and ``<img>`` tags without extra configuration.
        Use ``text``, ``html``, ``attr`` (+ ``attr=...``), ``href``,
        ``src`` or ``content`` for exact behavior.
    pattern:
        Regex applied to the extracted string; group 1 (or group 0) is kept.
    transform:
        Callable applied last (``parse_datetime``, ``to_float``,
        ``clean_text``, ...). For ``many`` fields it receives the whole list.
    absolute_url:
        Resolve relative URLs against the page URL.
    candidate override:
        Inside CSS mode a candidate may end with ``@attr`` to read an
        attribute just for that candidate, e.g. ``"[data-sku]@data-sku"``.
    """

    name: str = ""
    selector: str | Sequence[str] = ""
    mode: str = "css"
    source: str = "auto"
    attr: str = ""
    many: bool = False
    required: bool = False
    default: Any = None
    pattern: str | None = None
    transform: Callable[[Any], Any] | None = None
    absolute_url: bool = False

    def __post_init__(self) -> None:
        # Allow ``Field(["a", "b"])`` shorthand: a positional list/tuple is
        # the ordered selector candidate list (the field name comes from the
        # dict key or is set explicitly).
        if isinstance(self.name, (list, tuple)) and self.selector == "":
            self.selector = list(self.name)
            self.name = ""
        if self.mode not in _MODES:
            raise SchemaError(f"Field {self.name or '?'!r}: unknown mode {self.mode!r}. Choose from: {list(_MODES)}")
        if self.source not in _SOURCES:
            raise SchemaError(f"Field {self.name or '?'!r}: unknown source {self.source!r}. Choose from: {list(_SOURCES)}")
        if self.selector is None:
            self.selector = ""


def _field_from_spec(name: str, spec: str) -> Field:
    """Build a Field from the string grammar used by ``extract()``:
    ``"sel@attr"``, ``"sel.text" | "sel.html" | "sel.href" | "sel.src"``,
    or plain ``"sel"`` (defaults to text extraction).
    """
    css, prop = _split_field_spec(spec)
    if prop == "text":
        return Field(name=name, selector=css, source="text")
    if prop == "html":
        return Field(name=name, selector=css, source="html")
    if prop in ("href", "src"):
        return Field(name=name, selector=css, source=prop)
    return Field(name=name, selector=css, source="attr", attr=prop)


def _normalize_fields(owner: str, fields: Any) -> list[Field]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(fields, dict):
        pairs = list(fields.items())
    elif isinstance(fields, Iterable):
        for f in fields:
            if not isinstance(f, Field):
                raise SchemaError(f"Schema {owner!r}: fields must be Field objects or a dict of name -> spec.")
            pairs.append((f.name, f))
    else:
        raise SchemaError(f"Schema {owner!r}: fields must be a dict or a list of Field objects.")

    out: list[Field] = []
    seen: set[str] = set()
    for fname, spec in pairs:
        if not fname:
            raise SchemaError(f"Schema {owner!r}: every field needs a name.")
        if fname in seen:
            raise SchemaError(f"Schema {owner!r}: duplicate field {fname!r}.")
        seen.add(fname)
        if isinstance(spec, Field):
            f = dc_replace(spec, name=fname)
        elif isinstance(spec, str):
            f = _field_from_spec(fname, spec)
        elif isinstance(spec, list):
            # Ordered candidate selectors for one field.
            f = Field(name=fname, selector=spec)
        elif isinstance(spec, dict):
            f = Field(name=fname, **spec)
        else:
            raise SchemaError(f"Schema {owner!r}: invalid spec for field {fname!r}: {spec!r}")
        out.append(f)
    return out


def _candidates(f: Field) -> list[tuple[str, str | None]]:
    """Normalize the selector into (css_selector, attr_override) candidates."""
    sel = f.selector
    if sel is None or sel == "":
        specs: list[str] = [""]
    elif isinstance(sel, str):
        specs = [sel]
    else:
        specs = [str(s) for s in sel]
    out: list[tuple[str, str | None]] = []
    for s in specs:
        s = (s or "").strip()
        if f.mode == "css" and "@" in s:
            base, attr = s.rsplit("@", 1)
            out.append((base.strip(), attr.strip()))
        else:
            out.append((s, None))
    return out or [("", None)]


def _match(root: BeautifulSoup | Tag, spec: str, mode: str) -> list[Tag]:
    """Run one candidate selector against *root*; never raises on no match."""
    if not spec:
        return [root]  # type: ignore[list-item]
    if mode == "xpath":
        try:
            return select_xpath(root, spec)
        except SelectorError as exc:
            if "matched nothing" not in str(exc):
                raise SchemaError(f"Invalid XPath {spec!r}: {exc}") from exc
            return []
    if mode == "regex":
        return select_by_regex(root, spec)
    try:
        matched = list(root.select(spec))
        if matched:
            return matched
    except Exception as exc:  # soupsieve raises SelectorSyntaxError
        if ":" not in spec:
            raise SchemaError(f"Invalid CSS selector {spec!r}: {exc}") from exc
    # Fallback for XML tags, colons/namespaces (e.g. dc:creator, content:encoded, guid, pubDate)
    if hasattr(root, "find_all"):
        found = root.find_all(spec)
        if found:
            return list(found)
        if ":" in spec:
            prefix, local = spec.split(":", 1)
            found = root.find_all([local, spec, f"{prefix}:{local}"])
            if found:
                return list(found)
    return []


def _match_safe(root: BeautifulSoup | Tag, spec: str, mode: str) -> list[Tag] | None:
    """Like _match, but invalid selectors yield None so multi-candidate
    fields (e.g. namespaced RSS tags) degrade to the next candidate."""
    try:
        return _match(root, spec, mode)
    except SchemaError:
        return None


def _value_from_tag(tag: Tag, source: str, attr: str) -> str:
    if source == "auto":
        for a in ("content", "datetime", "value"):
            v = tag.get(a)
            if v:
                return _scalar(v)
        text = _tag_text(tag)
        if text:
            return text
        for a in ("src", "href"):
            v = tag.get(a)
            if v:
                return _scalar(v)
        return ""
    if source == "text":
        return _tag_text(tag)
    if source == "html":
        return str(tag)
    if source == "attr":
        return _scalar(tag.get(attr, "")) if attr else ""
    if source in ("href", "src", "content"):
        return _scalar(tag.get(source, ""))
    raise SchemaError(f"Unknown field source {source!r}. Choose from: {list(_SOURCES)}")


def _post_process(value: Any, f: Field, base_url: str, *, apply_transform: bool = True) -> tuple[bool, Any]:
    """pattern -> absolute_url -> transform. Returns (found, value)."""
    if value is None or value == "":
        return False, None
    if f.pattern:
        m = re.search(f.pattern, str(value))
        if not m:
            return False, None
        value = m.group(1) if m.groups else m.group(0)
    if f.absolute_url and base_url and isinstance(value, str):
        value = urljoin(base_url, value)
    if apply_transform and f.transform is not None:
        try:
            value = f.transform(value)
        except Exception as exc:
            raise SchemaError(f"Field '{f.name}': transform failed: {exc}") from exc
    if value is None or value == "":
        return False, None
    return True, value


def _resolve_field(root: BeautifulSoup | Tag, f: Field, base_url: str) -> tuple[bool, Any]:
    candidates = _candidates(f)

    if f.many:
        seen: set[int] = set()
        values: list[Any] = []
        for spec, cand_attr in candidates:
            matched = _match_safe(root, spec, f.mode)
            if matched is None:
                continue
            for tag in matched:
                if not isinstance(tag, Tag) or id(tag) in seen:
                    continue
                seen.add(id(tag))
                raw = _value_from_tag(tag, "attr" if cand_attr else f.source, cand_attr or f.attr)
                ok, v = _post_process(raw, f, base_url, apply_transform=False)
                if ok and v not in (None, ""):
                    values.append(v)
        if not values:
            return False, None
        if f.transform is not None:
            try:
                values = f.transform(values)
            except Exception as exc:
                raise SchemaError(f"Field '{f.name}': transform failed: {exc}") from exc
            if values is None or values == "":
                return False, None
        return True, values

    for spec, cand_attr in candidates:
        matched = _match_safe(root, spec, f.mode)
        if matched is None:
            continue
        for tag in matched:
            if not isinstance(tag, Tag):
                continue
            raw = _value_from_tag(tag, "attr" if cand_attr else f.source, cand_attr or f.attr)
            ok, v = _post_process(raw, f, base_url)
            if ok:
                return True, v
    return False, None


@dataclass
class Schema:
    """A reusable, named extraction recipe.

    fields:
        dict of ``name -> spec`` (``"h2"``, ``"a@href"``) or ``name -> Field``,
        or a plain list of named ``Field`` objects.
    item:
        CSS selector(s) for record roots. Empty = whole document as one
        record. When nothing matches, the page is (optionally) treated as a
        single record — index page and article page layouts can share one
        schema.
    strict:
        Raise :class:`SchemaError` when a required field is missing
        instead of falling back to ``default``.
    system_fields:
        Appended to every record; choose among ``schema``, ``source_url``,
        ``scraped_at`` (empty tuple to disable).
    """

    name: str
    fields: Sequence[Field] | dict[str, Any] = dc_field(default_factory=list)
    item: str | Sequence[str] = ""
    system_fields: Sequence[str] = _SYSTEM_FIELDS
    strict: bool = False
    fallback_single: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        self.fields = _normalize_fields(self.name, self.fields)
        unknown = [s for s in self.system_fields if s not in _SYSTEM_FIELDS]
        if unknown:
            raise SchemaError(
                f"Schema {self.name!r}: unknown system field(s) {unknown}. Allowed: {list(_SYSTEM_FIELDS)}"
            )
        clash = {f.name for f in self.fields} & set(self.system_fields)  # type: ignore[union-attr]
        if clash:
            raise SchemaError(
                f"Schema {self.name!r}: field name(s) {sorted(clash)} collide with system fields {list(self.system_fields)}."
            )

    def __repr__(self) -> str:
        return f"Schema(name={self.name!r}, fields={len(self.fields)}, item={self.item!r})"  # type: ignore[arg-type]

    # --- roots -------------------------------------------------------------

    def roots(self, soup: BeautifulSoup | Tag) -> list[Tag]:
        """Elements that each represent one record."""
        specs = [s.strip() for s in _as_list(self.item) if s and s.strip()]
        if not specs:
            return [soup]  # type: ignore[list-item]
        combined = ", ".join(specs)
        try:
            found = list(soup.select(combined))
        except Exception:  # noqa: BLE001  # soupsieve errors fall back to find_all below
            found = []
            for spec in specs:  # re-raise with the offending spec
                found.extend(soup.select(spec))
        if not found and hasattr(soup, "find_all"):
            for spec in specs:
                if not re.search(r"[.#\[:]", spec):
                    found.extend(soup.find_all(spec))
        if not found:
            if self.fallback_single:
                _log.warning(
                    "schema '%s': item selector %r matched nothing — treating the whole page as one record",
                    self.name, combined,
                )
                return [soup]  # type: ignore[list-item]
            return []
        return found

    # --- extraction --------------------------------------------------------

    def extract(
        self,
        soup: BeautifulSoup | Tag,
        *,
        base_url: str = "",
        roots: Sequence[Tag] | None = None,
        strict: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Extract one dict record per root element.

        ``roots`` overrides the ``item`` selector (used by
        ``Scraper.query(...).scrape(...)`` so query results become records).
        """
        strict_mode = self.strict if strict is None else strict
        item_roots = list(roots) if roots is not None else self.roots(soup)
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        records: list[dict[str, Any]] = []
        for root in item_roots:
            rec: dict[str, Any] = {}
            for f in self.fields:  # type: ignore[arg-type]
                found, value = _resolve_field(root, f, base_url)
                if not found:
                    if f.required and strict_mode:
                        tagname = getattr(root, "name", "root")
                        raise SchemaError(
                            f"Schema {self.name!r}: required field '{f.name}' not found in <{tagname}>."
                        )
                    value = f.default if f.default is not None else ([] if f.many else None)
                rec[f.name] = value
            for sysf in self.system_fields:
                if sysf == "schema":
                    rec[sysf] = self.name
                elif sysf == "source_url":
                    rec[sysf] = base_url
                elif sysf == "time_scraped":
                    rec[sysf] = scraped_at
                else:
                    rec[sysf] = scraped_at
            records.append(rec)
        return records

    def item_builder(self, base_url: str = "") -> Callable[[BeautifulSoup], list[Any]]:
        """Return ``soup -> [ScrapedItem, ...]`` for use with CrawlEngine."""
        from .models import ScrapedItem

        def build(soup: BeautifulSoup):
            items = []
            for rec in self.extract(soup, base_url=base_url):
                items.append(ScrapedItem(
                    tag=self.name,
                    text=str(rec.get("title") or rec.get("name") or ""),
                    url=str(rec.get("url") or ""),
                    meta=dict(rec),
                ))
            return items

        return build

    def derive(self, name: str, **changes: Any) -> Schema:
        """Copy this schema under a new name, e.g. site-specific tweaks::

            cnn = NEWS_SCHEMA.derive("cnn", item="article.teaser")
        """
        changes.setdefault("fields", self.fields)
        return dc_replace(self, name=name, **changes)


def _as_list(value: str | Sequence[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


# --- results ---------------------------------------------------------------

class SchemaResultSet(list):
    """Extracted schema records (plain dicts) with JSON/export helpers."""

    def __init__(self, records: Iterable[dict[str, Any]] = (), *, schema: str = "", source_url: str = ""):
        super().__init__(records)
        self.schema = schema
        self.source_url = source_url

    def __repr__(self) -> str:
        return f"SchemaResultSet({len(self)} records, schema={self.schema!r})"

    def to_list(self) -> list[dict[str, Any]]:
        return list(self)

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(list(self), indent=indent, ensure_ascii=False)

    def keys(self) -> list[str]:
        """Ordered union of record keys (useful for CSV columns / display)."""
        out: list[str] = []
        seen: set[str] = set()
        for rec in self:
            for k in rec:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        return out

    def export(self, fmt: str, dest: str | Path, **kwargs: Any) -> Path:
        from .exporters import export_csv, export_json, export_sqlite

        dest = Path(dest)
        dispatch = {
            "json": lambda: export_json(self, dest, **kwargs),
            "csv": lambda: export_csv(self, dest, **kwargs),
            "sqlite": lambda: export_sqlite(self, dest, **kwargs),
        }
        fn = dispatch.get(fmt.lower())
        if fn is None:
            raise ValueError(f"Unknown format '{fmt}'. Choose from: {list(dispatch)}")
        return fn()


def load_schema_from_json(path: str | Path) -> Schema:
    """Load a schema from a JSON file. Structure:
    {
      "name": "...", "item": "...", "fields": {"title": "h1", "author": {"selector": ".byline"}},
      ...
    }
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError(f"Schema JSON must be an object: {path}")
    if "name" not in data:
        raise SchemaError(f"Schema JSON missing 'name': {path}")
    return Schema(**data)


def schema_from_dict(data: dict[str, Any]) -> Schema:
    """Create a Schema instance from a plain dict (same shape as load_schema_from_json)."""
    if "name" not in data:
        raise SchemaError("Schema dict missing 'name'.")
    return Schema(**data)

_REGISTRY: dict[str, Schema] = {}


def register_schema(schema: Schema, *, replace: bool = False) -> Schema:
    """Register *schema* under its name for later ``scrape("name")`` lookups."""
    if not isinstance(schema, Schema):
        raise SchemaError(f"register_schema() expects a Schema, got {type(schema).__name__}.")
    if schema.name in _REGISTRY and not replace:
        raise SchemaError(
            f"Schema {schema.name!r} is already registered — pass replace=True to override."
        )
    _REGISTRY[schema.name] = schema
    return schema


def get_schema(name_or_schema: str | Schema) -> Schema:
    """Resolve a schema name (or pass a Schema instance through)."""
    if isinstance(name_or_schema, Schema):
        return name_or_schema
    try:
        return _REGISTRY[name_or_schema]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise SchemaError(
            f"Unknown schema {name_or_schema!r}. Registered schemas: {available}"
        ) from None


def list_schemas() -> list[str]:
    """Names of all registered schemas."""
    return sorted(_REGISTRY)


# --- built-in schemas ------------------------------------------------------

NEWS_SCHEMA = Schema(
    name="news",
    description="News stories on index/list pages or single article pages.",
    item=["article", ".post", ".story", ".news-item", ".news-card", ".article-card", ".teaser"],
    fields={
        "title": Field([
            "h1", ".entry-title", ".post-title", ".headline", ".title", "h2", "h3",
            "meta[property='og:title']",
        ]),
        "description": Field([
            "meta[property='og:description']", "meta[name='description']",
            ".excerpt", ".summary", ".dek", ".description", "p",
        ]),
        "url": Field([
            "link[rel='canonical']@href", "a.more@href", "h2 a@href", "h3 a@href", "a@href",
        ], absolute_url=True),
        "author": Field([
            "[rel='author']", "[itemprop='author']", ".author", ".byline",
            ".writer", "meta[name='author']",
        ]),
        "published_at": Field([
            "time", "[itemprop='datePublished']", "[datetime]",
            "meta[property='article:published_time']", ".date", ".published",
            ".timestamp", ".time",
        ], transform=parse_datetime),
        "image": Field(["meta[property='og:image']", "img"], absolute_url=True),
        "category": Field([".category", ".topic", "[rel='tag']", "meta[property='article:section']"]),
        "tags": Field(["a[rel='tag']", ".tags a", ".tag"], many=True),
        "id": Field(selector="", source="attr", attr="data-id"),
    },
)

ARTICLE_SCHEMA = Schema(
    name="article",
    description="Single long-form article page (whole document as one record).",
    item="",
    fields={
        "title": Field(["h1", "meta[property='og:title']", "meta[name='twitter:title']"]),
        "description": Field([
            "meta[property='og:description']", "meta[name='description']",
            ".excerpt", ".dek", "p",
        ]),
        "url": Field(["link[rel='canonical']@href", "meta[property='og:url']@content"], absolute_url=True),
        "author": Field([
            "[rel='author']", "[itemprop='author']", ".author", ".byline",
            "meta[name='author']", "a[href*='/author/']",
        ]),
        "published_at": Field([
            "meta[property='article:published_time']", "[itemprop='datePublished']",
            "time", ".published", ".date",
        ], transform=parse_datetime),
        "updated_at": Field([
            "meta[property='article:modified_time']", "[itemprop='dateModified']",
            ".updated", ".modified",
        ], transform=parse_datetime),
        "image": Field(["meta[property='og:image']", "meta[name='twitter:image']"], absolute_url=True),
        "content": Field([
            "article", "[itemprop='articleBody']", ".article-body",
            ".post-content", ".entry-content", "main",
        ], transform=clean_text),
        "tags": Field(["a[rel='tag']", ".tags a", ".post-tags a"], many=True),
        "site_name": Field(["meta[property='og:site_name']"]),
    },
)

PRODUCT_SCHEMA = Schema(
    name="product",
    description="Product listing cards or a single product page.",
    item=[".product", ".product-card", "li.product", "[itemtype*='Product']"],
    fields={
        "name": Field([
            "h1", ".product-title", ".product-name", "[itemprop='name']",
            "meta[property='og:title']",
        ]),
        "price": Field([
            "[itemprop='price']", ".price", ".sale-price", "span.price",
            "meta[property='product:price:amount']",
        ], transform=to_float),
        "currency": Field([
            "meta[property='product:price:currency']", "[itemprop='priceCurrency']", ".currency",
        ]),
        "url": Field([
            "link[rel='canonical']@href", "a.product-link@href",
            ".product-title a@href", "h1 a@href", "h2 a@href",
        ], absolute_url=True),
        "image": Field(["meta[property='og:image']", "img"], absolute_url=True),
        "sku": Field(["[itemprop='sku']", ".sku", "[data-sku]@data-sku", "#sku"]),
        "availability": Field([
            "[itemprop='availability']", ".availability", ".stock",
            "meta[property='product:availability']",
        ]),
        "rating": Field(["[itemprop='ratingValue']", ".rating", "[data-rating]@data-rating"], transform=to_float),
        "review_count": Field([
            "[itemprop='reviewCount']", ".review-count", ".ratings",
        ], transform=to_int),
        "brand": Field(["[itemprop='brand']", ".brand", "meta[property='product:brand:Name']"]),
        "description": Field([
            "meta[property='og:description']", "[itemprop='description']",
            ".description", "#description", "p",
        ]),
    },
)

RSS_SCHEMA = Schema(
    name="rss",
    description="RSS and Atom news feed stories.",
    item=["item", "entry"],
    fields={
        "article_id": Field([
            "guid", "id", "guid@isPermaLink", "entry > id", "item > id",
        ]),
        "title": Field([
            "title", "item > title", "entry > title",
        ]),
        "description": Field([
            "description", "summary", "content:encoded", "content",
        ]),
        "source_urls": Field([
            "link", "link@href", "guid", "id",
        ], absolute_url=True),
        "author_name": Field([
            "dc:creator", "author > name", "author", "creator",
        ]),
        "date_published": Field([
            "pubDate", "published", "updated", "dc:date", "date",
        ], transform=parse_datetime),
        "id": Field([
            "guid", "id", "guid@isPermaLink", "entry > id", "item > id",
        ]),
        "url": Field([
            "link", "link@href", "guid", "id",
        ], absolute_url=True),
        "author": Field([
            "dc:creator", "author > name", "author", "creator",
        ]),
        "published_at": Field([
            "pubDate", "published", "updated", "dc:date", "date",
        ], transform=parse_datetime),
    },
    system_fields=("schema", "source_url", "scraped_at", "time_scraped"),
)

FEED_SCHEMA = RSS_SCHEMA
NEWS_FEED_SCHEMA = RSS_SCHEMA

for _builtin in (NEWS_SCHEMA, ARTICLE_SCHEMA, PRODUCT_SCHEMA, RSS_SCHEMA):
    register_schema(_builtin)

_REGISTRY["feed"] = RSS_SCHEMA
_REGISTRY["news_feed"] = RSS_SCHEMA

