"""Tests for the element classifier."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from supescrapper.classifier import (
    ElementClassifier,
    ClassificationRule,
    AttrMatcher,
    get_classifier,
    INTERACTIVE_TYPES,
)


@pytest.fixture
def classifier() -> ElementClassifier:
    return ElementClassifier()


@pytest.fixture
def soup() -> BeautifulSoup:
    return BeautifulSoup("", "lxml")


def _tag(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestClassificationRule:
    def test_css_selector_match(self):
        rule = ClassificationRule(name="test", css_selectors=[".submit"])
        soup = _tag('<div class="submit"><button>OK</button></div>')
        div = soup.find("div")
        assert soup.select_one(".submit") is div
        assert rule.matches(div, soup)

    def test_css_selector_no_match(self):
        rule = ClassificationRule(name="test", css_selectors=[".submit"])
        soup = _tag('<div class="other"><button>OK</button></div>')
        div = soup.find("div")
        assert soup.select_one(".submit") is None
        assert not rule.matches(div, soup)

    def test_tag_name_match(self):
        rule = ClassificationRule(name="test", tag_name="button")
        tag = _tag("<button>OK</button>")
        assert rule.matches(tag.find("button"), tag)

    def test_tag_name_no_match(self):
        rule = ClassificationRule(name="test", tag_name="button")
        tag = _tag("<div>OK</div>")
        assert not rule.matches(tag.find("div"), tag)

    def test_tag_names_match(self):
        rule = ClassificationRule(name="test", tag_names=["button", "a"])
        tag1 = _tag("<button>OK</button>")
        tag2 = _tag("<a href='/'>Link</a>")
        assert rule.matches(tag1.find("button"), tag1)
        assert rule.matches(tag2.find("a"), tag2)

    def test_attr_matcher_exact(self):
        rule = ClassificationRule(
            name="test",
            attr_matchers=[AttrMatcher(attribute="type", exact="submit")],
        )
        tag = _tag('<input type="submit"/>')
        assert rule.matches(tag.find("input"), tag)

    def test_attr_matcher_contains(self):
        rule = ClassificationRule(
            name="test",
            attr_matchers=[AttrMatcher(attribute="class", contains="btn")],
        )
        tag = _tag('<button class="btn-primary">OK</button>')
        assert rule.matches(tag.find("button"), tag)

    def test_attr_matcher_pattern(self):
        rule = ClassificationRule(
            name="test",
            attr_matchers=[AttrMatcher(attribute="href", pattern=r"^https://")],
        )
        tag = _tag('<a href="https://example.com">Link</a>')
        assert rule.matches(tag.find("a"), tag)

    def test_text_pattern_match(self):
        rule = ClassificationRule(name="test", text_patterns=[r"submit|save"])
        tag = _tag("<button>Submit Form</button>")
        assert rule.matches(tag.find("button"), tag)

    def test_text_pattern_no_match(self):
        rule = ClassificationRule(name="test", text_patterns=[r"^exact$"])
        tag = _tag("<button>Not Exact</button>")
        assert not rule.matches(tag.find("button"), tag)

    def test_composite_check(self):
        rule = ClassificationRule(
            name="test",
            composite_check=lambda tag: tag.name == "dialog",
        )
        tag = _tag("<dialog>Modal</dialog>")
        assert rule.matches(tag.find("dialog"), tag)

    def test_multiple_attr_matchers_all_must_pass(self):
        rule = ClassificationRule(
            name="test",
            attr_matchers=[
                AttrMatcher(attribute="type", exact="text"),
                AttrMatcher(attribute="name", contains="user"),
            ],
        )
        tag = _tag('<input type="text" name="username"/>')
        assert rule.matches(tag.find("input"), tag)

        tag2 = _tag('<input type="text" name="email"/>')
        assert not rule.matches(tag2.find("input"), tag2)


class TestElementClassifier:
    def test_button_detection(self, classifier: ElementClassifier, soup: BeautifulSoup):
        soup = _tag("<button>Click me</button>")
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "button" in types

    def test_submit_button_detection(self, classifier: ElementClassifier):
        soup = _tag('<button type="submit">Submit</button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "submit_button" in types
        assert "button" in types

    def test_input_button_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="button" value="Click"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "button" in types

    def test_role_button_detection(self, classifier: ElementClassifier):
        soup = _tag('<div role="button" tabindex="0">Click</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "button" in types

    def test_toggle_button_detection(self, classifier: ElementClassifier):
        soup = _tag('<button aria-pressed="false">Toggle</button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "toggle_button" in types

    def test_switch_detection(self, classifier: ElementClassifier):
        soup = _tag('<div role="switch" aria-checked="false">Switch</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "toggle_button" in types

    def test_hamburger_detection(self, classifier: ElementClassifier):
        soup = _tag('<button class="hamburger-menu">Menu</button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "hamburger" in types

    def test_hamburger_aria_label(self, classifier: ElementClassifier):
        soup = _tag('<button aria-label="Toggle menu">☰</button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "hamburger" in types

    def test_icon_button_detection(self, classifier: ElementClassifier):
        soup = _tag('<button aria-label="Close"><svg></svg></button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "icon_button" in types

    def test_text_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="text" name="q"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "text_input" in types

    def test_default_input_is_text(self, classifier: ElementClassifier):
        soup = _tag('<input name="q"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "text_input" in types

    def test_email_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="email"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "email_input" in types

    def test_password_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="password"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "password_input" in types

    def test_search_input_by_type(self, classifier: ElementClassifier):
        soup = _tag('<input type="search"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "search_input" in types

    def test_search_input_by_placeholder(self, classifier: ElementClassifier):
        soup = _tag('<input placeholder="Search..."/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "search_input" in types

    def test_search_input_by_name(self, classifier: ElementClassifier):
        soup = _tag('<input name="search_query"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "search_input" in types

    def test_number_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="number"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "number_input" in types

    def test_date_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="date"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "date_input" in types

    def test_tel_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="tel"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "tel_input" in types

    def test_url_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="url"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "url_input" in types

    def test_file_input_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="file"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "file_input" in types

    def test_range_slider_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="range" min="0" max="100"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "range_slider" in types

    def test_textarea_detection(self, classifier: ElementClassifier):
        soup = _tag("<textarea>Enter text</textarea>")
        tag = soup.find("textarea")
        types = classifier.classify(tag, soup)
        assert "textarea" in types

    def test_select_dropdown_detection(self, classifier: ElementClassifier):
        soup = _tag("<select><option>A</option></select>")
        tag = soup.find("select")
        types = classifier.classify(tag, soup)
        assert "select_dropdown" in types

    def test_checkbox_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="checkbox"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "checkbox" in types

    def test_checkbox_role_detection(self, classifier: ElementClassifier):
        soup = _tag('<div role="checkbox" aria-checked="false">Check</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "checkbox" in types

    def test_radio_detection(self, classifier: ElementClassifier):
        soup = _tag('<input type="radio" name="choice"/>')
        tag = soup.find("input")
        types = classifier.classify(tag, soup)
        assert "radio" in types

    def test_form_detection(self, classifier: ElementClassifier):
        soup = _tag('<form action="/submit"><input type="text"/></form>')
        tag = soup.find("form")
        types = classifier.classify(tag, soup)
        assert "form" in types

    def test_link_detection(self, classifier: ElementClassifier):
        soup = _tag('<a href="/page">Link</a>')
        tag = soup.find("a")
        types = classifier.classify(tag, soup)
        assert "link" in types

    def test_nav_detection(self, classifier: ElementClassifier):
        soup = _tag("<nav><a href='/'>Home</a></nav>")
        tag = soup.find("nav")
        types = classifier.classify(tag, soup)
        assert "nav" in types

    def test_nav_role_detection(self, classifier: ElementClassifier):
        soup = _tag('<div role="navigation"><a href="/">Home</a></div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "nav" in types

    def test_breadcrumb_detection(self, classifier: ElementClassifier):
        soup = _tag('<nav aria-label="breadcrumb"><ol><li>Home</li></ol></nav>')
        tag = soup.find("nav")
        types = classifier.classify(tag, soup)
        assert "breadcrumb" in types

    def test_tabs_detection(self, classifier: ElementClassifier):
        soup = _tag('<div role="tablist"><button role="tab">Tab 1</button></div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "tabs" in types

    def test_pagination_detection(self, classifier: ElementClassifier):
        soup = _tag('<nav aria-label="Pagination"><a href="?p=1">1</a></nav>')
        tag = soup.find("nav")
        types = classifier.classify(tag, soup)
        assert "pagination" in types

    def test_modal_dialog_tag(self, classifier: ElementClassifier):
        soup = _tag("<dialog>Modal content</dialog>")
        tag = soup.find("dialog")
        types = classifier.classify(tag, soup)
        assert "modal" in types

    def test_modal_role(self, classifier: ElementClassifier):
        soup = _tag('<div role="dialog" aria-modal="true">Modal</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "modal" in types

    def test_modal_class(self, classifier: ElementClassifier):
        soup = _tag('<div class="modal-content">Modal</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "modal" in types

    def test_header_detection(self, classifier: ElementClassifier):
        soup = _tag("<header>Site header</header>")
        tag = soup.find("header")
        types = classifier.classify(tag, soup)
        assert "header" in types

    def test_footer_detection(self, classifier: ElementClassifier):
        soup = _tag("<footer>Site footer</footer>")
        tag = soup.find("footer")
        types = classifier.classify(tag, soup)
        assert "footer" in types

    def test_sidebar_detection(self, classifier: ElementClassifier):
        soup = _tag("<aside>Sidebar content</aside>")
        tag = soup.find("aside")
        types = classifier.classify(tag, soup)
        assert "sidebar" in types

    def test_sidebar_class_detection(self, classifier: ElementClassifier):
        soup = _tag('<div class="sidebar-left">Content</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "sidebar" in types

    def test_card_detection_by_class(self, classifier: ElementClassifier):
        soup = _tag('<div class="card"><h2>Title</h2></div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "card" in types

    def test_card_detection_by_structure(self, classifier: ElementClassifier):
        soup = _tag('''<div>
            <h3>Title</h3>
            <img src="photo.jpg" alt="Photo"/>
            <p>Description</p>
            <a href="/more">Read more</a>
        </div>''')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "card" in types

    def test_table_detection(self, classifier: ElementClassifier):
        soup = _tag("<table><tr><th>Name</th></tr><tr><td>Alice</td></tr></table>")
        tag = soup.find("table")
        types = classifier.classify(tag, soup)
        assert "table" in types

    def test_image_detection(self, classifier: ElementClassifier):
        soup = _tag('<img src="photo.jpg" alt="Photo"/>')
        tag = soup.find("img")
        types = classifier.classify(tag, soup)
        assert "image" in types

    def test_avatar_detection(self, classifier: ElementClassifier):
        soup = _tag('<img class="user-avatar" src="me.jpg" alt="Me"/>')
        tag = soup.find("img")
        types = classifier.classify(tag, soup)
        assert "avatar" in types

    def test_logo_detection(self, classifier: ElementClassifier):
        soup = _tag('<img class="logo" src="logo.png" alt="Brand"/>')
        tag = soup.find("img")
        types = classifier.classify(tag, soup)
        assert "logo" in types

    def test_icon_svg_detection(self, classifier: ElementClassifier):
        soup = _tag("<svg class='icon'><circle/></svg>")
        tag = soup.find("svg")
        types = classifier.classify(tag, soup)
        assert "icon" in types

    def test_video_detection(self, classifier: ElementClassifier):
        soup = _tag("<video><source src='video.mp4'/></video>")
        tag = soup.find("video")
        types = classifier.classify(tag, soup)
        assert "video" in types

    def test_alert_detection(self, classifier: ElementClassifier):
        soup = _tag('<div role="alert">Warning!</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "alert" in types

    def test_alert_class_detection(self, classifier: ElementClassifier):
        soup = _tag('<div class="notification">Info</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "alert" in types

    def test_accordion_detection(self, classifier: ElementClassifier):
        soup = _tag("<details><summary>Click to expand</summary><p>Content</p></details>")
        tag = soup.find("details")
        types = classifier.classify(tag, soup)
        assert "accordion" in types

    def test_carousel_detection(self, classifier: ElementClassifier):
        soup = _tag('<div class="swiper-carousel"><div class="slide">Slide 1</div></div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "carousel" in types

    def test_carousel_aria_detection(self, classifier: ElementClassifier):
        soup = _tag('<div aria-roledescription="carousel" role="region"><div aria-roledescription="slide">1</div></div>')
        tag = soup.find("div", attrs={"aria-roledescription": "carousel"})
        types = classifier.classify(tag, soup)
        assert "carousel" in types

    def test_social_link_detection(self, classifier: ElementClassifier):
        soup = _tag('<a href="https://github.com/user">GitHub</a>')
        tag = soup.find("a")
        types = classifier.classify(tag, soup)
        assert "social_link" in types
        assert "link" in types

    def test_share_button_detection(self, classifier: ElementClassifier):
        soup = _tag('<a class="share-btn" href="https://twitter.com/intent/tweet">Share</a>')
        tag = soup.find("a")
        types = classifier.classify(tag, soup)
        assert "share_button" in types

    def test_login_form_detection(self, classifier: ElementClassifier):
        soup = _tag('''<form action="/login" id="login-form">
            <input type="email" name="email"/>
            <input type="password" name="password"/>
            <button type="submit">Log In</button>
        </form>''')
        tag = soup.find("form")
        types = classifier.classify(tag, soup)
        assert "login_form" in types
        assert "form" in types

    def test_login_form_by_password_heuristic(self, classifier: ElementClassifier):
        soup = _tag('''<form>
            <input type="password"/>
            <button type="submit">Sign In</button>
        </form>''')
        tag = soup.find("form")
        types = classifier.classify(tag, soup)
        assert "login_form" in types

    def test_signup_form_detection(self, classifier: ElementClassifier):
        soup = _tag('<form action="/register"><input name="email"/><button>Sign Up</button></form>')
        tag = soup.find("form")
        types = classifier.classify(tag, soup)
        assert "signup_form" in types

    def test_search_bar_detection(self, classifier: ElementClassifier):
        soup = _tag('<form role="search"><input type="search"/></form>')
        tag = soup.find("form")
        types = classifier.classify(tag, soup)
        assert "search_bar" in types

    def test_search_bar_class_detection(self, classifier: ElementClassifier):
        soup = _tag('<div class="search-form-wrapper"><input/></div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "search_bar" in types

    def test_social_auth_button(self, classifier: ElementClassifier):
        soup = _tag('<button>Sign in with Google</button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "social_auth_button" in types

    def test_add_to_cart_detection(self, classifier: ElementClassifier):
        soup = _tag('<button class="add-to-cart-btn">Add to Cart</button>')
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "add_to_cart" in types

    def test_price_display_detection(self, classifier: ElementClassifier):
        soup = _tag('<span class="product-price">$29.99</span>')
        tag = soup.find("span")
        types = classifier.classify(tag, soup)
        assert "price_display" in types

    def test_price_display_by_symbol(self, classifier: ElementClassifier):
        soup = _tag('<span>€45.00</span>')
        tag = soup.find("span")
        types = classifier.classify(tag, soup)
        assert "price_display" in types

    def test_element_with_no_classification(self, classifier: ElementClassifier):
        soup = _tag("<div>Just a plain div</div>")
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert types == []


class TestFindMethods:
    def test_find_buttons(self, classifier: ElementClassifier):
        soup = _tag("<button>A</button><button>B</button><div>C</div>")
        results = classifier.find("button", soup)
        assert len(results) == 2

    def test_find_interactive(self, classifier: ElementClassifier):
        soup = _tag('''
            <button>Click</button>
            <input type="text"/>
            <a href="/">Link</a>
            <div>Not interactive</div>
        ''')
        results = classifier.find_interactive(soup)
        assert len(results) == 3

    def test_find_all_types(self, classifier: ElementClassifier):
        soup = _tag("<button>A</button><a href='/'>Link</a>")
        result = classifier.find_all_types(soup)
        assert "button" in result
        assert "link" in result


class TestCustomRules:
    def test_register_custom_rule(self, classifier: ElementClassifier):
        rule = ClassificationRule(
            name="custom_widget",
            tag_name="div",
            attr_matchers=[AttrMatcher(attribute="class", contains="widget")],
            priority=100,
        )
        classifier.register(rule)
        assert "custom_widget" in classifier.registered_types

        soup = _tag('<div class="widget">Widget content</div>')
        tag = soup.find("div")
        types = classifier.classify(tag, soup)
        assert "custom_widget" in types

    def test_unregister_rule(self, classifier: ElementClassifier):
        assert classifier.unregister("button")
        assert "button" not in classifier.registered_types
        assert not classifier.unregister("nonexistent")

    def test_custom_rule_with_text_pattern(self, classifier: ElementClassifier):
        rule = ClassificationRule(
            name="danger_button",
            text_patterns=[r"delete|remove|destroy"],
            priority=95,
        )
        classifier.register(rule)
        soup = _tag("<button>Delete Account</button>")
        tag = soup.find("button")
        types = classifier.classify(tag, soup)
        assert "danger_button" in types

    def test_priority_ordering(self):
        c = ElementClassifier()
        c.register(ClassificationRule(name="low", priority=1))
        c.register(ClassificationRule(name="high", priority=100))
        assert c.registered_types[0] == "high"


class TestGetClassifier:
    def test_returns_fresh_instances(self):
        c1 = get_classifier()
        c2 = get_classifier()
        assert c1 is not c2

    def test_registration_does_not_leak(self):
        c1 = get_classifier()
        c1.register(ClassificationRule(name="custom_only"))
        c2 = get_classifier()
        assert "custom_only" in c1.registered_types
        assert "custom_only" not in c2.registered_types

    def test_has_builtin_rules(self):
        c = get_classifier()
        assert len(c.registered_types) > 25
        assert "button" in c.registered_types
        assert "login_form" in c.registered_types
        assert "search_input" in c.registered_types


class TestInteractiveTypes:
    def test_contains_essential_types(self):
        assert "button" in INTERACTIVE_TYPES
        assert "text_input" in INTERACTIVE_TYPES
        assert "link" in INTERACTIVE_TYPES
        assert "select_dropdown" in INTERACTIVE_TYPES
        assert "checkbox" in INTERACTIVE_TYPES
        assert "textarea" in INTERACTIVE_TYPES
