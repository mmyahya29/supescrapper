from __future__ import annotations

import re
from typing import Callable

from .models import ScrapedItem, ScrapedResultSet


FilterFn = Callable[[ScrapedItem], bool]


def _has_text(item: ScrapedItem, value: str) -> bool:
    return value.lower() in item.text.lower()


def _text_equals(item: ScrapedItem, value: str) -> bool:
    return item.text.strip().lower() == value.strip().lower()


def _text_matches(item: ScrapedItem, value: str) -> bool:
    return bool(re.search(value, item.text, re.IGNORECASE))


def _attr_has(item: ScrapedItem, attr: str, value: str) -> bool:
    actual = item.attrs.get(attr, "")
    return value.lower() in actual.lower()


def _attr_equals(item: ScrapedItem, attr: str, value: str) -> bool:
    return item.attrs.get(attr, "").strip().lower() == value.strip().lower()


def _attr_matches(item: ScrapedItem, attr: str, value: str) -> bool:
    return bool(re.search(value, item.attrs.get(attr, ""), re.IGNORECASE))


class QueryChain:
    """Chain-able filter applied to a ScrapedResultSet."""

    def __init__(self, results: ScrapedResultSet):
        self._results = results
        self._filters: list[FilterFn] = []

    @property
    def filters(self) -> list[FilterFn]:
        """Read-only view of the registered filter functions."""
        return list(self._filters)

    def matches(self, item: ScrapedItem) -> bool:
        return all(fn(item) for fn in self._filters)

    def _add(self, fn: FilterFn) -> "QueryChain":
        self._filters.append(fn)
        return self

    def _apply(self) -> ScrapedResultSet:
        items = list(self._results)
        for fn in self._filters:
            items = [i for i in items if fn(i)]
        out = ScrapedResultSet(items)
        out.source_url = self._results.source_url
        return out

    def where_text(self, op: str, value: str) -> "QueryChain":
        ops = {"contains": _has_text, "equals": _text_equals, "matches": _text_matches}
        fn = ops.get(op)
        if fn is None:
            raise ValueError(f"Unknown text operator '{op}'. Choose from: {list(ops)}")
        return self._add(lambda item, v=value, f=fn: f(item, v))

    def where_attr(self, attr: str, op: str, value: str) -> "QueryChain":
        ops = {"contains": _attr_has, "equals": _attr_equals, "matches": _attr_matches}
        fn = ops.get(op)
        if fn is None:
            raise ValueError(f"Unknown attr operator '{op}'. Choose from: {list(ops)}")
        return self._add(lambda item, a=attr, v=value, f=fn: f(item, a, v))

    def where_tag(self, tag_name: str) -> "QueryChain":
        return self._add(lambda item: item.tag == tag_name.lower())

    def where_url(self, op: str, value: str) -> "QueryChain":
        ops = {"contains": lambda i, v: v in i.url, "equals": lambda i, v: i.url == v, "matches": lambda i, v: bool(re.search(v, i.url))}
        fn = ops.get(op)
        if fn is None:
            raise ValueError(f"Unknown url operator '{op}'. Choose from: {list(ops)}")
        return self._add(lambda item, v=value, f=fn: f(item, v))

    def where(self, predicate: Callable[[ScrapedItem], bool]) -> "QueryChain":
        return self._add(predicate)

    def limit(self, n: int) -> ScrapedResultSet:
        result = self._apply()
        out = ScrapedResultSet(list(result)[:n])
        out.source_url = result.source_url
        return out

    def build(self) -> ScrapedResultSet:
        return self._apply()
