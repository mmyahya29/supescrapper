"""Registry of per-site RSS schemas.

JSON files in this directory define how to extract fields from each site's
RSS/Atom feed. Each file registers a schema under its ``name`` so the batch
scraper can pick the right extraction rules per source.

Example (bloomberg.json):

    {
      "name": "bloomberg",
      "item": "item",
      "fields": {
        "article_id": ["guid"],
        "title": ["title"],
        "description": ["description"],
        "url": ["link"],
        "author_name": ["dc:creator"],
        "date_published": ["pubDate"]
      }
    }

Usage:

    from supescrapper import get_schema, register_schema
    from examples.site_configs.registry import load_all

    load_all()  # registers every *.json in this directory
    sch = get_schema("bloomberg")
"""
from __future__ import annotations

import json
from pathlib import Path

from supescrapper import Schema, register_schema

_DIR = Path(__file__).parent


def load_config(path: str | Path) -> Schema:
    """Load a single schema config JSON file and register it."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Schema config must be a JSON object: {p}")
    if "name" not in data:
        raise TypeError(f"Schema config missing 'name': {p}")
    # Strip keys that Schema doesn't accept (e.g. "_comment" docs).
    valid = {
        "name", "fields", "item", "system_fields", "strict",
        "fallback_single", "description",
    }
    clean = {k: v for k, v in data.items() if k in valid}
    schema = Schema(**clean)
    return register_schema(schema)


def load_all() -> list[Schema]:
    """Load and register every ``*.json`` schema config in this directory."""
    loaded: list[Schema] = []
    for p in sorted(_DIR.glob("*.json")):
        try:
            loaded.append(load_config(p))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] failed to load schema config {p.name}: {exc}")
    return loaded
