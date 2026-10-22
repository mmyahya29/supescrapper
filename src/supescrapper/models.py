from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScrapedItem:
    tag: str = ""
    text: str = ""
    html: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    classification: list[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        if key in ("text", "html", "tag", "url", "classification"):
            return getattr(self, key)
        if key == "attrs":
            return self.attrs
        return self.meta.get(key, self.attrs.get(key))

    def to_dict(self) -> dict[str, Any]:
        # Attributes and meta cannot overwrite core fields (tag/text/url
        # would otherwise be spoofable by e.g. <div text="...">).
        d: dict[str, Any] = {}
        d.update(self.attrs)
        d.update(self.meta)
        d["tag"] = self.tag
        d["text"] = self.text
        d["url"] = self.url
        if self.classification:
            d["classification"] = self.classification
        return d

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScrapedItem":
        known_fields = {"tag", "text", "html", "url", "classification", "attrs"}
        meta = {k: v for k, v in data.items() if k not in known_fields}
        return cls(
            tag=data.get("tag", ""),
            text=data.get("text", ""),
            html=data.get("html", ""),
            attrs=data.get("attrs", {}),
            url=data.get("url", ""),
            meta=meta,
            classification=data.get("classification", []),
        )




class ScrapedResultSet:
    """Ordered collection of ScrapedItems with chained query support."""

    def __init__(self, items: list[ScrapedItem] | None = None):
        self._items: list[ScrapedItem] = items or []
        self._source_url: str = ""

    @property
    def source_url(self) -> str:
        return self._source_url

    @source_url.setter
    def source_url(self, url: str) -> None:
        self._source_url = url

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, idx: int) -> ScrapedItem:
        return self._items[idx]

    def __repr__(self) -> str:
        return f"ScrapedResultSet({len(self._items)} items)"

    def first(self) -> ScrapedItem | None:
        return self._items[0] if self._items else None

    def last(self) -> ScrapedItem | None:
        return self._items[-1] if self._items else None

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items]

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_list(), indent=indent, ensure_ascii=False)

    def export(self, fmt: str, dest: str | Path, **kwargs: Any) -> Path:
        from .exporters import export_json, export_csv, export_sqlite

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
