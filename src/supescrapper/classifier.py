"""
Intelligent DOM element classifier.

Detects 30+ element types using multi-signal heuristics:
  ARIA roles → semantic tags → input types → attributes → classes → text patterns → structure.

Extensible via custom ClassificationRule registration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from bs4 import BeautifulSoup, Tag


INTERACTIVE_TYPES = frozenset({
    "button", "submit_button", "reset_button", "toggle_button",
    "icon_button", "hamburger", "link", "text_input", "email_input",
    "password_input", "search_input", "number_input", "date_input",
    "tel_input", "url_input", "textarea", "select_dropdown",
    "checkbox", "radio", "file_input", "range_slider",
})


@dataclass
class AttrMatcher:
    attribute: str
    pattern: str | None = None
    exact: str | None = None
    contains: str | None = None
    case_insensitive: bool = True

    def matches(self, tag: Tag) -> bool:
        val = tag.get(self.attribute, "")
        if val is None:
            val = ""
        val = str(val)
        flags = re.IGNORECASE if self.case_insensitive else 0
        if self.exact is not None:
            return val.strip().lower() == self.exact.lower() if self.case_insensitive else val.strip() == self.exact
        if self.contains is not None:
            return self.contains.lower() in val.lower() if self.case_insensitive else self.contains in val
        if self.pattern is not None:
            return bool(re.search(self.pattern, val, flags))
        return bool(val)


@dataclass
class ClassificationRule:
    name: str
    css_selectors: list[str] = field(default_factory=list)
    tag_names: list[str] | None = None
    tag_name: str | None = None
    attr_matchers: list[AttrMatcher] | None = None
    text_patterns: list[str] | None = None
    priority: int = 0
    description: str = ""
    composite_check: Callable[[Tag], bool] | None = None

    def matches(self, tag: Tag, soup: BeautifulSoup) -> bool:
        if self.tag_name and tag.name != self.tag_name:
            return False

        if self.tag_names and tag.name not in self.tag_names:
            return False

        if self.css_selectors:
            css_matched = False
            for sel in self.css_selectors:
                if tag.select_one(sel) is not None:
                    css_matched = True
                    break
                for ancestor in tag.parents:
                    if isinstance(ancestor, BeautifulSoup):
                        if ancestor.select_one(sel) is tag:
                            css_matched = True
                            break
                if css_matched:
                    break
            if not css_matched and not (self.tag_name or self.tag_names or self.attr_matchers or self.text_patterns):
                return False

        if self.attr_matchers:
            for matcher in self.attr_matchers:
                if not matcher.matches(tag):
                    return False

        if self.text_patterns:
            text = tag.get_text(separator=" ", strip=True)
            if not text:
                return False
            for pat in self.text_patterns:
                if not re.search(pat, text, re.IGNORECASE):
                    return False

        if self.composite_check and not self.composite_check(tag):
            return False

        return True


class ElementClassifier:
    def __init__(self) -> None:
        self._rules: list[ClassificationRule] = list(_BUILTIN_RULES)

    def register(self, rule: ClassificationRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def unregister(self, name: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    def classify(self, tag: Tag, soup: BeautifulSoup | None = None) -> list[str]:
        if soup is None:
            soup = tag if isinstance(tag, BeautifulSoup) else tag.find_parent() or tag
            if not isinstance(soup, BeautifulSoup):
                soup = BeautifulSoup("", "lxml")

        types: list[str] = []
        for rule in self._rules:
            try:
                if rule.matches(tag, soup):
                    types.append(rule.name)
            except Exception:
                continue
        return types

    def classify_all(self, soup: BeautifulSoup) -> dict[int, list[str]]:
        result: dict[int, list[str]] = {}
        for tag in soup.find_all(True):
            types = self.classify(tag, soup)
            if types:
                result[id(tag)] = types
        return result

    def find(self, element_type: str, soup: BeautifulSoup) -> list[Tag]:
        matches: list[Tag] = []
        for tag in soup.find_all(True):
            types = self.classify(tag, soup)
            if element_type in types:
                matches.append(tag)
        return matches

    def find_interactive(self, soup: BeautifulSoup) -> list[Tag]:
        found: set[int] = set()
        result: list[Tag] = []
        for tag in soup.find_all(True):
            types = self.classify(tag, soup)
            for t in types:
                if t in INTERACTIVE_TYPES and id(tag) not in found:
                    found.add(id(tag))
                    result.append(tag)
                    break
        return result

    def find_all_types(self, soup: BeautifulSoup) -> dict[str, list[Tag]]:
        result: dict[str, list[Tag]] = {}
        for tag in soup.find_all(True):
            types = self.classify(tag, soup)
            for t in types:
                result.setdefault(t, []).append(tag)
        return result

    @property
    def registered_types(self) -> list[str]:
        return [r.name for r in self._rules]


def _composite_button(tag: Tag) -> bool:
    if tag.name == "button":
        return True
    if tag.name == "input":
        return tag.get("type", "submit") in ("submit", "button", "reset", "image")
    if tag.get("role") in ("button", "switch", "menuitem", "menuitemcheckbox", "menuitemradio"):
        return True
    if tag.get("aria-pressed") is not None:
        return True
    return False


def _composite_submit_button(tag: Tag) -> bool:
    if tag.name == "button" and tag.get("type") == "submit":
        return True
    if tag.name == "input" and tag.get("type") == "submit":
        return True
    return False


def _composite_toggle_button(tag: Tag) -> bool:
    if tag.get("aria-pressed") is not None:
        return True
    if tag.get("role") in ("switch", "toggle"):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"toggle|switch", cls, re.I):
        return True
    return False


def _composite_hamburger(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    if re.search(r"hamburger|burger|menu-toggle|mobile-menu|nav-toggle|menu-btn", cls, re.I):
        return True
    label = (tag.get("aria-label", "") or "").lower()
    if label in ("menu", "toggle menu", "open menu", "navigation menu"):
        return True
    controls = (tag.get("aria-controls", "") or "").lower()
    if "menu" in controls and tag.name in ("button", "a"):
        return True
    return False


def _composite_icon_button(tag: Tag) -> bool:
    if tag.name != "button" and tag.get("role") != "button":
        return False
    text = tag.get_text(strip=True)
    if not text and tag.find(["svg", "img", "i", "span", "icon"]):
        return True
    if tag.get("aria-label") and not text:
        return True
    return False


def _composite_link(tag: Tag) -> bool:
    if tag.name == "a" and tag.get("href"):
        return True
    if tag.get("role") == "link":
        return True
    return False


def _composite_text_input(tag: Tag) -> bool:
    if tag.name != "input":
        return False
    t = tag.get("type", "text").lower()
    return t in ("text", "")


def _composite_email_input(tag: Tag) -> bool:
    return tag.name == "input" and tag.get("type", "").lower() == "email"


def _composite_password_input(tag: Tag) -> bool:
    return tag.name == "input" and tag.get("type", "").lower() == "password"


def _composite_search_input(tag: Tag) -> bool:
    if tag.name != "input":
        return False
    if tag.get("type", "").lower() == "search":
        return True
    for attr in ("placeholder", "name", "id", "aria-label"):
        val = (tag.get(attr, "") or "").lower()
        if "search" in val:
            return True
    if tag.get("role") in ("searchbox", "combobox"):
        return True
    return False


def _composite_number_input(tag: Tag) -> bool:
    return tag.name == "input" and tag.get("type", "").lower() == "number"


def _composite_date_input(tag: Tag) -> bool:
    if tag.name != "input":
        return False
    return tag.get("type", "").lower() in ("date", "datetime-local", "month", "week", "time")


def _composite_tel_input(tag: Tag) -> bool:
    if tag.name != "input":
        return False
    t = tag.get("type", "").lower()
    return t == "tel"


def _composite_url_input(tag: Tag) -> bool:
    return tag.name == "input" and tag.get("type", "").lower() == "url"


def _composite_file_input(tag: Tag) -> bool:
    return tag.name == "input" and tag.get("type", "").lower() == "file"


def _composite_range_slider(tag: Tag) -> bool:
    return tag.name == "input" and tag.get("type", "").lower() == "range"


def _composite_textarea(tag: Tag) -> bool:
    if tag.name == "textarea":
        return True
    if tag.get("contenteditable", "").lower() in ("true", "plaintext-only"):
        if tag.get("role") in ("textbox", None):
            aria_multiline = tag.get("aria-multiline", "")
            if aria_multiline != "false":
                return True
    return False


def _composite_select_dropdown(tag: Tag) -> bool:
    if tag.name == "select":
        return True
    if tag.get("role") in ("listbox", "combobox", "tree", "treeitem"):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"dropdown|select-picker|combobox|typeahead|autocomplete", cls, re.I):
        return True
    return False


def _composite_checkbox(tag: Tag) -> bool:
    if tag.name == "input" and tag.get("type") == "checkbox":
        return True
    if tag.get("role") in ("checkbox", "switch", "menuitemcheckbox"):
        return True
    return False


def _composite_radio(tag: Tag) -> bool:
    if tag.name == "input" and tag.get("type") == "radio":
        return True
    if tag.get("role") in ("radio", "menuitemradio"):
        return True
    return False


def _composite_form(tag: Tag) -> bool:
    if tag.name == "form":
        return True
    if tag.get("role") == "form":
        return True
    return False


def _composite_nav(tag: Tag) -> bool:
    if tag.name == "nav":
        return True
    if tag.get("role") in ("navigation", "menubar"):
        return True
    label = (tag.get("aria-label", "") or "").lower()
    if any(kw in label for kw in ("nav", "menu", "main", "header")):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"navbar|nav-bar|navigation|main-nav|site-nav", cls, re.I):
        return True
    return False


def _composite_breadcrumb(tag: Tag) -> bool:
    label = (tag.get("aria-label", "") or "").lower()
    if "breadcrumb" in label:
        return True
    cls = " ".join(tag.get("class", []))
    if "breadcrumb" in cls.lower():
        return True
    return False


def _composite_tabs(tag: Tag) -> bool:
    if tag.get("role") in ("tablist", "tab", "tabpanel"):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"tab-list|tabbar|nav-tabs", cls, re.I):
        return True
    return False


def _composite_pagination(tag: Tag) -> bool:
    label = (tag.get("aria-label", "") or "").lower()
    if "pagination" in label:
        return True
    cls = " ".join(tag.get("class", []))
    if "pagination" in cls.lower():
        return True
    if tag.find(attrs={"aria-current": "page"}):
        return True
    return False


def _composite_modal(tag: Tag) -> bool:
    if tag.name == "dialog":
        return True
    if tag.get("role") in ("dialog", "alertdialog"):
        return True
    if tag.get("aria-modal") == "true":
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"modal|dialog|lightbox|popup", cls, re.I):
        return True
    return False


def _composite_header(tag: Tag) -> bool:
    if tag.name == "header":
        return True
    if tag.get("role") == "banner":
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"site-header|page-header|main-header|top-header", cls, re.I):
        return True
    return False


def _composite_footer(tag: Tag) -> bool:
    if tag.name == "footer":
        return True
    if tag.get("role") == "contentinfo":
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"site-footer|page-footer|main-footer", cls, re.I):
        return True
    return False


def _composite_sidebar(tag: Tag) -> bool:
    if tag.name == "aside":
        return True
    if tag.get("role") == "complementary":
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"sidebar|side-bar|drawer|off-canvas", cls, re.I):
        return True
    return False


def _composite_card(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    if re.search(r"\bcard\b", cls, re.I):
        return True
    if tag.name == "article" and tag.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
        return True
    has_heading = bool(tag.find(["h1", "h2", "h3", "h4", "h5", "h6"]))
    has_image = bool(tag.find("img"))
    has_text = bool(tag.find("p"))
    has_link = bool(tag.find("a"))
    has_button = bool(tag.find("button"))
    if has_heading and (has_image or has_text) and (has_link or has_button):
        return True
    return False


def _composite_table(tag: Tag) -> bool:
    if tag.name == "table":
        return True
    if tag.get("role") in ("table", "grid"):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"data-table|datatable", cls, re.I):
        return True
    return False


def _composite_image(tag: Tag) -> bool:
    if tag.name == "img":
        return True
    if tag.name == "picture":
        return True
    return False


def _composite_avatar(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    src = (tag.get("src", "") or "").lower()
    alt = (tag.get("alt", "") or "").lower()
    combined = f"{cls} {src} {alt}"
    if re.search(r"avatar|profile-pic|user-pic|author-photo|headshot", combined):
        return True
    return False


def _composite_logo(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    alt = (tag.get("alt", "") or "").lower()
    combined = f"{cls} {alt}"
    if re.search(r"\blogo\b|brand|wordmark", combined):
        return True
    return False


def _composite_icon(tag: Tag) -> bool:
    if tag.name == "svg":
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"\bicon\b|fa-|glyphicon|symbol|favicon", cls, re.I):
        return True
    if tag.name in ("i",) and re.search(r"fa-|glyphicon|icon", cls, re.I):
        return True
    if tag.get("role") == "presentation" and tag.name == "img":
        return True
    return False


def _composite_video(tag: Tag) -> bool:
    if tag.name == "video":
        return True
    if tag.name == "audio":
        return True
    if tag.name == "iframe":
        src = (tag.get("src", "") or "").lower()
        if any(d in src for d in ("youtube", "youtu.be", "vimeo", "dailymotion", "twitch")):
            return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"video-player|media-player|video-wrapper|plyr|video-js|jwplayer", cls, re.I):
        return True
    return False


def _composite_search_bar(tag: Tag) -> bool:
    if tag.name == "form" and tag.get("role") == "search":
        return True
    if tag.name == "search":
        return True
    if tag.get("role") == "search":
        return True
    action = (tag.get("action", "") or "").lower()
    if "search" in action:
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"search-bar|search-form|searchbox|search-field|search-wrapper", cls, re.I):
        return True
    return False


def _composite_alert(tag: Tag) -> bool:
    if tag.get("role") in ("alert", "status", "alertdialog"):
        return True
    if tag.get("aria-live") in ("polite", "assertive"):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"\balert\b|notice|notification|toast|snackbar|banner|warning", cls, re.I):
        return True
    return False


def _composite_accordion(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    if re.search(r"accordion|collapsible|expandable|collapse-panel", cls, re.I):
        return True
    if tag.name == "details":
        return True
    if tag.get("role") == "region" and tag.get("aria-expanded") is not None:
        return True
    if tag.get("aria-expanded") is not None and tag.name in ("button", "h2", "h3", "h4"):
        return True
    return False


def _composite_carousel(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    if re.search(r"carousel|slider|slideshow|swiper|slick|owl-carousel|glide|splide", cls, re.I):
        return True
    if tag.get("aria-roledescription") == "carousel":
        return True
    label = (tag.get("aria-label", "") or "").lower()
    if "carousel" in label or "slider" in label:
        return True
    return False


def _composite_social_link(tag: Tag) -> bool:
    if tag.name != "a":
        return False
    href = (tag.get("href", "") or "").lower()
    for domain in SOCIAL_DOMAINS:
        if domain in href:
            return True
    return False


def _composite_share_button(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", []))
    label = (tag.get("aria-label", "") or "").lower()
    title = (tag.get("title", "") or "").lower()
    combined = f"{cls} {label} {title}"
    if re.search(r"share|sharing|social-share", combined, re.I):
        return True
    if tag.name == "a":
        href = (tag.get("href", "") or "").lower()
        for pat in ("facebook.com/sharer", "twitter.com/intent", "x.com/intent",
                     "linkedin.com/sharing", "pinterest.com/pin/create", "reddit.com/submit"):
            if pat in href:
                return True
    return False


def _composite_login_form(tag: Tag) -> bool:
    if tag.name != "form" and tag.get("role") != "form":
        return False
    action = (tag.get("action", "") or "").lower()
    form_id = (tag.get("id", "") or "").lower()
    cls = " ".join(tag.get("class", []))
    combined = f"{action} {form_id} {cls}"
    if re.search(r"login|signin|sign-in|log-in|auth", combined, re.I):
        return True
    has_password = bool(tag.find("input", attrs={"type": "password"}))
    if has_password:
        submit = tag.find("button", attrs={"type": "submit"}) or tag.find("input", attrs={"type": "submit"})
        if submit:
            submit_text = submit.get_text(strip=True).lower() if submit.name == "button" else (submit.get("value", "") or "").lower()
            if any(kw in submit_text for kw in ("login", "log in", "sign in", "submit", "enter")):
                return True
    return False


def _composite_signup_form(tag: Tag) -> bool:
    if tag.name != "form" and tag.get("role") != "form":
        return False
    action = (tag.get("action", "") or "").lower()
    form_id = (tag.get("id", "") or "").lower()
    cls = " ".join(tag.get("class", []))
    combined = f"{action} {form_id} {cls}"
    if re.search(r"signup|sign-up|register|registration|create-account", combined, re.I):
        return True
    return False


def _composite_social_auth_button(tag: Tag) -> bool:
    if tag.name not in ("button", "a") and tag.get("role") != "button":
        return False
    text = tag.get_text(strip=True).lower()
    cls = " ".join(tag.get("class", [])).lower()
    combined = f"{text} {cls}"
    if re.search(r"sign\s*in\s*with|log\s*in\s*with|continue\s*with", combined, re.I):
        return True
    if re.search(r"google|github|facebook|twitter|apple|microsoft|linkedin|oauth", combined, re.I):
        if re.search(r"sign|log|continue|connect", combined, re.I):
            return True
    return False


def _composite_add_to_cart(tag: Tag) -> bool:
    if tag.name not in ("button", "a") and tag.get("role") != "button":
        return False
    text = tag.get_text(strip=True).lower()
    cls = " ".join(tag.get("class", [])).lower()
    combined = f"{text} {cls}"
    if re.search(r"add\s*to\s*cart|buy\s*now|add\s*to\s*bag|add\s*to\s*basket", combined, re.I):
        return True
    return False


def _composite_price_display(tag: Tag) -> bool:
    text = tag.get_text(strip=True)
    if re.search(r"[\$£€¥]\s*\d+[\.,]?\d*", text):
        return True
    cls = " ".join(tag.get("class", []))
    if re.search(r"\bprice\b|cost|amount", cls, re.I):
        if re.search(r"\d", text):
            return True
    return False


SOCIAL_DOMAINS = [
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "youtube.com", "github.com", "tiktok.com",
    "pinterest.com", "reddit.com", "threads.net", "mastodon",
    "t.me", "wa.me", "snapchat.com", "medium.com",
    "dribbble.com", "behance.net", "codepen.io",
]

_SOCIAL_PATTERN = "|".join(re.escape(d) for d in SOCIAL_DOMAINS)

_BUILTIN_RULES: list[ClassificationRule] = [
    ClassificationRule(
        name="submit_button",
        composite_check=_composite_submit_button,
        priority=90,
        description="Submit buttons (type=submit)",
    ),
    ClassificationRule(
        name="toggle_button",
        composite_check=_composite_toggle_button,
        priority=88,
        description="Toggle buttons with aria-pressed or switch role",
    ),
    ClassificationRule(
        name="hamburger",
        composite_check=_composite_hamburger,
        priority=87,
        description="Hamburger / mobile menu toggles",
    ),
    ClassificationRule(
        name="icon_button",
        composite_check=_composite_icon_button,
        priority=85,
        description="Icon-only buttons (SVG/img content, no text)",
    ),
    ClassificationRule(
        name="button",
        composite_check=_composite_button,
        priority=80,
        description="All buttons: <button>, input[type=button|submit|reset|image], role=button",
    ),
    ClassificationRule(
        name="search_input",
        composite_check=_composite_search_input,
        priority=78,
        description="Search input fields",
    ),
    ClassificationRule(
        name="password_input",
        composite_check=_composite_password_input,
        priority=77,
        description="Password input fields",
    ),
    ClassificationRule(
        name="email_input",
        composite_check=_composite_email_input,
        priority=76,
        description="Email input fields",
    ),
    ClassificationRule(
        name="tel_input",
        composite_check=_composite_tel_input,
        priority=75,
        description="Telephone input fields",
    ),
    ClassificationRule(
        name="url_input",
        composite_check=_composite_url_input,
        priority=74,
        description="URL input fields",
    ),
    ClassificationRule(
        name="date_input",
        composite_check=_composite_date_input,
        priority=73,
        description="Date/time input fields",
    ),
    ClassificationRule(
        name="number_input",
        composite_check=_composite_number_input,
        priority=72,
        description="Number input fields",
    ),
    ClassificationRule(
        name="file_input",
        composite_check=_composite_file_input,
        priority=71,
        description="File upload input fields",
    ),
    ClassificationRule(
        name="range_slider",
        composite_check=_composite_range_slider,
        priority=70,
        description="Range slider inputs",
    ),
    ClassificationRule(
        name="text_input",
        composite_check=_composite_text_input,
        priority=65,
        description="Text input fields (default type)",
    ),
    ClassificationRule(
        name="textarea",
        composite_check=_composite_textarea,
        priority=64,
        description="Textareas and contenteditable divs",
    ),
    ClassificationRule(
        name="select_dropdown",
        composite_check=_composite_select_dropdown,
        priority=63,
        description="Select dropdowns and custom comboboxes",
    ),
    ClassificationRule(
        name="checkbox",
        composite_check=_composite_checkbox,
        priority=62,
        description="Checkboxes and switches",
    ),
    ClassificationRule(
        name="radio",
        composite_check=_composite_radio,
        priority=61,
        description="Radio buttons and radio groups",
    ),
    ClassificationRule(
        name="login_form",
        composite_check=_composite_login_form,
        priority=85,
        description="Login / sign-in forms",
    ),
    ClassificationRule(
        name="signup_form",
        composite_check=_composite_signup_form,
        priority=84,
        description="Registration / sign-up forms",
    ),
    ClassificationRule(
        name="form",
        composite_check=_composite_form,
        priority=40,
        description="Generic forms",
    ),
    ClassificationRule(
        name="social_auth_button",
        composite_check=_composite_social_auth_button,
        priority=86,
        description="Social authentication buttons (Sign in with Google, etc.)",
    ),
    ClassificationRule(
        name="add_to_cart",
        composite_check=_composite_add_to_cart,
        priority=83,
        description="Add-to-cart / buy-now buttons",
    ),
    ClassificationRule(
        name="price_display",
        composite_check=_composite_price_display,
        priority=82,
        description="Price display elements",
    ),
    ClassificationRule(
        name="search_bar",
        composite_check=_composite_search_bar,
        priority=75,
        description="Search bar containers and forms",
    ),
    ClassificationRule(
        name="modal",
        composite_check=_composite_modal,
        priority=70,
        description="Modals, dialogs, lightboxes, popups",
    ),
    ClassificationRule(
        name="breadcrumb",
        composite_check=_composite_breadcrumb,
        priority=68,
        description="Breadcrumb navigation",
    ),
    ClassificationRule(
        name="tabs",
        composite_check=_composite_tabs,
        priority=67,
        description="Tab interfaces",
    ),
    ClassificationRule(
        name="pagination",
        composite_check=_composite_pagination,
        priority=66,
        description="Pagination controls",
    ),
    ClassificationRule(
        name="nav",
        composite_check=_composite_nav,
        priority=55,
        description="Navigation bars, menus, nav elements",
    ),
    ClassificationRule(
        name="header",
        composite_check=_composite_header,
        priority=50,
        description="Page and section headers",
    ),
    ClassificationRule(
        name="footer",
        composite_check=_composite_footer,
        priority=50,
        description="Page and section footers",
    ),
    ClassificationRule(
        name="sidebar",
        composite_check=_composite_sidebar,
        priority=48,
        description="Sidebars, drawers, off-canvas panels",
    ),
    ClassificationRule(
        name="card",
        composite_check=_composite_card,
        priority=45,
        description="Cards, tiles, article-like containers",
    ),
    ClassificationRule(
        name="table",
        composite_check=_composite_table,
        priority=50,
        description="Data tables and grids",
    ),
    ClassificationRule(
        name="carousel",
        composite_check=_composite_carousel,
        priority=52,
        description="Carousels, sliders, slideshows",
    ),
    ClassificationRule(
        name="accordion",
        composite_check=_composite_accordion,
        priority=53,
        description="Accordions, collapsible sections, details/summary",
    ),
    ClassificationRule(
        name="alert",
        composite_check=_composite_alert,
        priority=54,
        description="Alerts, notifications, toasts, banners",
    ),
    ClassificationRule(
        name="video",
        composite_check=_composite_video,
        priority=46,
        description="Video/audio elements and embedded players",
    ),
    ClassificationRule(
        name="link",
        composite_check=_composite_link,
        priority=35,
        description="Hyperlinks (<a href>, role=link)",
    ),
    ClassificationRule(
        name="social_link",
        composite_check=_composite_social_link,
        priority=60,
        description="Links to social media profiles",
    ),
    ClassificationRule(
        name="share_button",
        composite_check=_composite_share_button,
        priority=58,
        description="Social sharing buttons",
    ),
    ClassificationRule(
        name="image",
        composite_check=_composite_image,
        priority=30,
        description="Images and picture elements",
    ),
    ClassificationRule(
        name="avatar",
        composite_check=_composite_avatar,
        priority=47,
        description="User avatar / profile images",
    ),
    ClassificationRule(
        name="logo",
        composite_check=_composite_logo,
        priority=46,
        description="Brand logos and wordmarks",
    ),
    ClassificationRule(
        name="icon",
        composite_check=_composite_icon,
        priority=25,
        description="Icon elements (SVG, Font Awesome, Glyphicons)",
    ),
]

_default_classifier: ElementClassifier | None = None


def get_classifier() -> ElementClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = ElementClassifier()
    return _default_classifier
