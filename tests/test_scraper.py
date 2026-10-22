"""Tests for the Scraper/engine API using local HTML (no network)."""
from __future__ import annotations

import pytest

from supescrapper import Scraper
from supescrapper.models import ScrapedResultSet
from supescrapper.exceptions import SelectorError

PAGE = """
<html><body>
  <nav aria-label="main nav"><a href="/a">A</a><a href="/b">B</a></nav>
  <button>OK</button>
  <input type="search" placeholder="Search">
  <div class="card"><h2>Widget</h2><span class="price">$9.99</span>
    <a href="/widget">More</a></div>
</body></html>
"""


class TestFindElements:
    def test_returns_resultset_not_scraper(self):
        r = Scraper().from_html(PAGE).find_elements("button")
        assert isinstance(r, ScrapedResultSet)
        assert len(r) == 1
        assert r[0].text == "OK"

    def test_resultset_is_iterable(self):
        r = Scraper().from_html(PAGE).find_elements("link")
        assert len(list(r)) == len(r)

    def test_find_interactive_returns_resultset(self):
        r = Scraper().from_html(PAGE).find_interactive()
        assert isinstance(r, ScrapedResultSet)
        assert len(r) >= 3

    def test_find_before_load_raises(self):
        with pytest.raises(SelectorError):
            Scraper().find_elements("button")


class TestClassifyWithOriginalTags:
    def test_query_then_classify_uses_original_tags(self):
        html = '<div><nav aria-label="main nav"><a href="/x">L</a></nav></div>'
        res = Scraper().from_html(html).query("nav").classify().extract()
        assert "nav" in res[0].classification

    def test_classify_ancestor_rule(self):
        html = '<form class="login"><input type="password"><button type="submit">Log in</button></form>'
        res = Scraper().from_html(html).query("form").classify().extract()
        assert "login_form" in res[0].classification


class TestExtractNamedFields:
    def test_default_text_property(self):
        res = Scraper().from_html(PAGE).query(".card").extract(
            name="h2", price="span.price", link="a@href")
        assert res[0]["name"] == "Widget"
        assert res[0]["price"] == "$9.99"
        assert res[0]["link"] == "/widget"

    def test_explicit_text_property(self):
        res = Scraper().from_html(PAGE).query(".card").extract(name="h2.text")
        assert res[0]["name"] == "Widget"


class TestContextManager:
    def test_sync_context_manager(self):
        with Scraper().from_html(PAGE) as sc:
            assert len(sc.query("button").extract()) == 1


class TestSessionLifecycle:
    def test_close_is_idempotent(self):
        sc = Scraper()
        sc.close()
        sc.close()


class TestClassifierIsolation:
    def test_register_does_not_leak_across_instances(self):
        from supescrapper.classifier import ClassificationRule
        s1 = Scraper()
        s1.register_classifier(ClassificationRule(name="my_rule"))
        s2 = Scraper()
        assert "my_rule" in s1._classifier.registered_types
        assert "my_rule" not in s2._classifier.registered_types
