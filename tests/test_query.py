"""Tests for the query module."""
from __future__ import annotations

import pytest

from supescrapper.models import ScrapedItem, ScrapedResultSet
from supescrapper.query import QueryChain


def _items(*texts: str) -> ScrapedResultSet:
    items = [ScrapedItem(text=t, tag="p", html=f"<p>{t}</p>", url=f"https://example.com/{t}") for t in texts]
    return ScrapedResultSet(items)


def _items_with_attrs(**kwargs_list: list[str]) -> ScrapedResultSet:
    items = []
    for i, (attr, vals) in enumerate(kwargs_list.items()):
        for v in vals:
            items.append(ScrapedItem(tag="div", text=f"item-{i}", attrs={attr: v}))
    return ScrapedResultSet(items)


class TestWhereText:
    def test_contains(self):
        results = _items("Hello World", "Goodbye", "Hello Again")
        chain = QueryChain(results)
        chain.where_text("contains", "Hello")
        out = chain.build()
        assert len(out) == 2

    def test_equals(self):
        results = _items("Hello", "world", "HELLO", "World")
        chain = QueryChain(results)
        chain.where_text("equals", "hello")
        out = chain.build()
        assert len(out) == 2  # Hello and HELLO (case-insensitive match)

    def test_matches(self):
        results = _items("Price: $29.99", "No price", "$5.00 deal")
        chain = QueryChain(results)
        chain.where_text("matches", r"\$\d+\.\d{2}")
        out = chain.build()
        assert len(out) == 2

    def test_invalid_op_raises(self):
        results = _items("test")
        chain = QueryChain(results)
        with pytest.raises(ValueError, match="Unknown text operator"):
            chain.where_text("invalid", "val")


class TestWhereAttr:
    def test_contains(self):
        items = [
            ScrapedItem(tag="a", text="A", attrs={"href": "/blog/post-1"}),
            ScrapedItem(tag="a", text="B", attrs={"href": "/about"}),
            ScrapedItem(tag="a", text="C", attrs={"href": "/blog/post-2"}),
        ]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_attr("href", "contains", "/blog")
        out = chain.build()
        assert len(out) == 2

    def test_equals(self):
        items = [
            ScrapedItem(tag="a", text="A", attrs={"href": "/home"}),
            ScrapedItem(tag="a", text="B", attrs={"href": "/about"}),
        ]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_attr("href", "equals", "/home")
        out = chain.build()
        assert len(out) == 1

    def test_matches(self):
        items = [
            ScrapedItem(tag="a", text="A", attrs={"href": "/user/123"}),
            ScrapedItem(tag="a", text="B", attrs={"href": "/user/456"}),
            ScrapedItem(tag="a", text="C", attrs={"href": "/about"}),
        ]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_attr("href", "matches", r"/user/\d+")
        out = chain.build()
        assert len(out) == 2

    def test_missing_attr(self):
        items = [ScrapedItem(tag="div", text="No href")]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_attr("href", "contains", "test")
        out = chain.build()
        assert len(out) == 0


class TestWhereTag:
    def test_filter_by_tag(self):
        items = [
            ScrapedItem(tag="h1", text="Title"),
            ScrapedItem(tag="p", text="Para"),
            ScrapedItem(tag="h1", text="Subtitle"),
        ]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_tag("h1")
        out = chain.build()
        assert len(out) == 2


class TestWhereUrl:
    def test_contains(self):
        items = [
            ScrapedItem(tag="a", text="A", url="https://example.com/blog/1"),
            ScrapedItem(tag="a", text="B", url="https://example.com/about"),
            ScrapedItem(tag="a", text="C", url="https://example.com/blog/2"),
        ]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_url("contains", "/blog")
        out = chain.build()
        assert len(out) == 2


class TestWhere:
    def test_custom_predicate(self):
        results = _items("short", "this is a long text", "ok")
        chain = QueryChain(results)
        chain.where(lambda item: len(item.text) > 10)
        out = chain.build()
        assert len(out) == 1
        assert out[0].text == "this is a long text"


class TestLimit:
    def test_limits_results(self):
        results = _items("a", "b", "c", "d", "e")
        chain = QueryChain(results)
        out = chain.limit(3)
        assert len(out) == 3
        assert out[0].text == "a"


class TestChaining:
    def test_multiple_filters(self):
        items = [
            ScrapedItem(tag="a", text="Blog 1", attrs={"href": "/blog/1"}),
            ScrapedItem(tag="a", text="Blog 2", attrs={"href": "/blog/2"}),
            ScrapedItem(tag="a", text="About", attrs={"href": "/about"}),
            ScrapedItem(tag="p", text="Blog intro", attrs={"href": "/blog"}),
        ]
        results = ScrapedResultSet(items)
        chain = QueryChain(results)
        chain.where_tag("a").where_attr("href", "contains", "/blog")
        out = chain.build()
        assert len(out) == 2

    def test_build_preserves_source_url(self):
        results = _items("a", "b")
        results.source_url = "https://example.com"
        chain = QueryChain(results)
        out = chain.build()
        assert out.source_url == "https://example.com"
