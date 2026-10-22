"""Tests for the parser module."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from supescrapper.parser import (
    parse, is_spa, select, select_xpath, select_by_regex,
    tags_to_items, extract_fields, extract_table, extract_links,
    extract_images, search_text, _tag_url,
)
from supescrapper.models import ScrapedItem
from supescrapper.exceptions import SelectorError


class TestParse:
    def test_returns_beautifulsoup(self):
        soup = parse("<html><body><h1>Hello</h1></body></html>")
        assert isinstance(soup, BeautifulSoup)

    def test_parses_simple_html(self):
        soup = parse("<html><body><h1>Hello</h1></body></html>")
        assert soup.find("h1").get_text() == "Hello"


class TestIsSpa:
    def test_empty_root_div(self):
        assert is_spa('<html><body><div id="root"></div></body></html>')

    def test_next_root(self):
        assert is_spa('<html><body><div id="__next"></div></body></html>')

    def test_low_text_ratio(self):
        parts = "<p></p>" * 200
        html = "<html><body>" + parts + "</body></html>"
        assert len(html) > 500
        assert is_spa(html)

    def test_react_marker(self):
        assert is_spa('<html><body><div data-reactroot></div><script>"react"</script></body></html>')

    def test_webpack_assets(self):
        assert is_spa('<html><body><script type="module" src="/assets/app.abc123.js"></script></body></html>')

    def test_normal_page_not_spa(self):
        assert not is_spa("<html><body><h1>Welcome</h1><p>This is a normal page with lots of content.</p></body></html>")

    # --- false-positive regression tests ---
    def test_article_about_react_not_spa(self):
        html = ('<html><body><article>' + "x" * 600 +
                '<p>React is a UI library</p><script>"react"</script></article></body></html>')
        assert not is_spa(html)

    def test_json_ld_next_key_not_spa(self):
        html = ('<html><body><div>content ' + "y" * 600 +
                '</div><script type="application/ld+json">{"next": "/page2"}</script></body></html>')
        assert not is_spa(html)

    def test_article_mentioning_vite_not_spa(self):
        html = '<html><body><p>' + "z" * 600 + '</p><p>"vite" is a build tool</p></body></html>'
        assert not is_spa(html)

    def test_ssr_next_page_with_content_not_spa(self):
        html = ('<html><body><div id="__next">' + ("<p>Server rendered content.</p>" * 20) +
                '</div><script id="__NEXT_DATA__">{}</script></body></html>')
        assert not is_spa(html)

    def test_next_shell_with_no_content_is_spa(self):
        html = '<html><body><div id="__next"></div><script id="__NEXT_DATA__">{}</script></body></html>'
        assert is_spa(html)

    def test_spinner_only_root_is_spa(self):
        html = '<html><body><div id="root"><!-- comment --></div></body></html>'
        assert is_spa(html)



class TestSelect:
    def test_css_selector(self):
        soup = parse("<html><body><h1>A</h1><h1>B</h1></body></html>")
        results = select(soup, "h1")
        assert len(results) == 2

    def test_css_no_match_raises(self):
        soup = parse("<html><body><h1>A</h1></body></html>")
        with pytest.raises(SelectorError, match="matched nothing"):
            select(soup, "h2")


class TestSelectXpath:
    def test_xpath_selector(self):
        soup = parse("<html><body><h1>A</h1><h1>B</h1></body></html>")
        results = select_xpath(soup, "//h1")
        assert len(results) == 2
        assert [t.get_text() for t in results] == ["A", "B"]

    def test_xpath_returns_correct_element_not_first(self):
        html = ('<html><body><div class="a"><h1>First</h1></div>'
                '<div class="b"><h1>Second</h1></div></body></html>')
        soup = parse(html)
        results = select_xpath(soup, "//div[@class='b']//h1")
        assert len(results) == 1
        assert results[0].get_text() == "Second"

    def test_xpath_preserves_attributes(self):
        soup = parse('<html><body><a href="/x" class="link">L</a></body></html>')
        results = select_xpath(soup, "//a")
        assert results[0].get("href") == "/x"
        assert "link" in results[0].get("class")

    def test_xpath_no_duplicates(self):
        html = '<html><body><h1>A</h1><h1>B</h1><h1>C</h1></body></html>'
        soup = parse(html)
        results = select_xpath(soup, "//h1")
        assert len(results) == len(set(t.get_text() for t in results))

    def test_xpath_invalid_expression_raises(self):
        soup = parse("<html><body><h1>A</h1></body></html>")
        with pytest.raises(SelectorError):
            select_xpath(soup, "//h1[")

    def test_xpath_no_match_raises(self):
        soup = parse("<html><body><h1>A</h1></body></html>")
        with pytest.raises(SelectorError, match="matched nothing"):
            select_xpath(soup, "//h2")


class TestSelectByRegex:
    def test_regex_match(self):
        soup = parse("<html><body><p>Price: 29.99 dollars</p><p>No price here</p></body></html>")
        results = select_by_regex(soup, r"\d+\.\d{2}")
        assert len(results) >= 1

    def test_regex_no_match(self):
        soup = parse("<html><body><p>No price</p></body></html>")
        results = select_by_regex(soup, r"\$\d+")
        assert len(results) == 0


class TestTagsToItems:
    def test_converts_to_scraped_items(self):
        soup = parse('<a href="/page">Link text</a>')
        tags = soup.find_all("a")
        items = tags_to_items(tags, base_url="https://example.com")
        assert len(items) == 1
        assert isinstance(items[0], ScrapedItem)
        assert items[0].tag == "a"
        assert items[0].text == "Link text"
        assert items[0].url == "https://example.com/page"

    def test_relative_url_resolved(self):
        soup = parse('<a href="/about">About</a>')
        tags = soup.find_all("a")
        items = tags_to_items(tags, base_url="https://example.com")
        assert items[0].url == "https://example.com/about"

    def test_absolute_url_not_modified(self):
        soup = parse('<a href="https://other.com/page">Link</a>')
        tags = soup.find_all("a")
        items = tags_to_items(tags, base_url="https://example.com")
        assert items[0].url == "https://other.com/page"

    def test_mailto_not_resolved(self):
        soup = parse('<a href="mailto:test@example.com">Email</a>')
        tags = soup.find_all("a")
        items = tags_to_items(tags, base_url="https://example.com")
        assert items[0].url == "mailto:test@example.com"

    def test_empty_url_for_no_href(self):
        soup = parse("<span>No link</span>")
        tags = soup.find_all("span")
        items = tags_to_items(tags)
        assert items[0].url == ""


class TestExtractFields:
    def test_text_field(self):
        soup = parse('<div><h2 class="title">My Title</h2></div>')
        div = soup.find("div")
        fields = extract_fields(div, title="h2.title.text")
        assert fields["title"] == "My Title"

    def test_href_field(self):
        soup = parse('<div><a href="/page">Link</a></div>')
        div = soup.find("div")
        fields = extract_fields(div, link="a@href")
        assert fields["link"] == "/page"

    def test_missing_field_returns_empty(self):
        soup = parse('<div><p>Text</p></div>')
        div = soup.find("div")
        fields = extract_fields(div, missing="h2.text")
        assert fields["missing"] == ""

    def test_default_property_is_text(self):
        soup = parse('<div><span class="x">Hello</span></div>')
        div = soup.find("div")
        fields = extract_fields(div, content="span.x.text")
        assert fields["content"] == "Hello"

    def test_class_selector_defaults_to_text(self):
        soup = parse('<div class="product"><span class="price">$9.99</span></div>')
        div = soup.find("div")
        fields = extract_fields(div, price="span.price")
        assert fields["price"] == "$9.99"

    def test_descendant_selector_without_explicit_prop(self):
        soup = parse('<div class="product"><h2>Widget</h2></div>')
        div = soup.find("div")
        fields = extract_fields(div, name=".product h2")
        assert fields["name"] == "Widget"

    def test_attribute_any_name_via_at(self):
        soup = parse('<div><a href="/p" data-id="7">Link</a></div>')
        div = soup.find("div")
        fields = extract_fields(div, ident="a@data-id", link="a@href")
        assert fields["ident"] == "7"
        assert fields["link"] == "/p"


class TestExtractTable:
    def test_basic_table(self):
        html = """
        <table>
            <tr><th>Name</th><th>Age</th></tr>
            <tr><td>Alice</td><td>30</td></tr>
            <tr><td>Bob</td><td>25</td></tr>
        </table>
        """
        soup = parse(html)
        data = extract_table(soup)
        assert len(data) == 2
        assert data[0] == {"Name": "Alice", "Age": "30"}
        assert data[1] == {"Name": "Bob", "Age": "25"}

    def test_no_table_raises(self):
        soup = parse("<html><body><p>No table</p></body></html>")
        with pytest.raises(SelectorError, match="No table found"):
            extract_table(soup)


class TestExtractLinks:
    def test_finds_all_links(self):
        soup = parse('<a href="/a">A</a><a href="/b">B</a>')
        items = extract_links(soup)
        assert len(items) == 2


class TestExtractImages:
    def test_finds_all_images(self):
        soup = parse('<img src="a.jpg"/><img src="b.png"/>')
        items = extract_images(soup)
        assert len(items) == 2


class TestSearchText:
    def test_finds_text(self):
        soup = parse("<html><body><p>Hello World</p><p>Goodbye</p></body></html>")
        items = search_text(soup, "Hello")
        assert len(items) == 1
        assert items[0].text == "Hello World"

    def test_case_insensitive_by_default(self):
        soup = parse("<html><body><p>Hello World</p></body></html>")
        items = search_text(soup, "hello")
        assert len(items) == 1

    def test_case_sensitive(self):
        soup = parse("<html><body><p>Hello World</p></body></html>")
        items = search_text(soup, "hello", case_sensitive=True)
        assert len(items) == 0

    def test_returns_deepest_elements(self):
        soup = parse("<html><body><div><p>Found me</p></div></body></html>")
        items = search_text(soup, "Found")
        assert len(items) == 1
        assert items[0].tag == "p"

    def test_no_match(self):
        soup = parse("<html><body><p>Nope</p></body></html>")
        items = search_text(soup, "missing")
        assert len(items) == 0


class TestTagUrl:
    def test_href(self):
        soup = parse('<a href="/page">Link</a>')
        tag = soup.find("a")
        assert _tag_url(tag) == "/page"

    def test_src(self):
        soup = parse('<img src="image.jpg"/>')
        tag = soup.find("img")
        assert _tag_url(tag) == "image.jpg"

    def test_data_src(self):
        soup = parse('<img data-src="lazy.jpg"/>')
        tag = soup.find("img")
        assert _tag_url(tag) == "lazy.jpg"

    def test_no_url(self):
        soup = parse("<div>No URL</div>")
        tag = soup.find("div")
        assert _tag_url(tag) == ""
