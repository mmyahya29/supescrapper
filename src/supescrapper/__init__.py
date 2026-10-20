"""
supescrapper — Universal web DOM scraper.

Quick start:
    from supescrapper import Scraper

    results = Scraper().fetch("https://example.com").query("h1").extract()
    results.export("json", "output.json")
"""

from .scraper import Scraper
from .async_scraper import AsyncScraper
from .models import ScrapedItem, ScrapedResultSet
from .query import QueryChain
from .classifier import (
    ClassificationRule,
    ElementClassifier,
    AttrMatcher,
    get_classifier,
    INTERACTIVE_TYPES,
)
from .exceptions import (
    SupescrapperError,
    FetchError,
    SelectorError,
    ExportError,
    DynamicPageError,
)

__version__ = "0.2.0"

__all__ = [
    "Scraper",
    "AsyncScraper",
    "ScrapedItem",
    "ScrapedResultSet",
    "QueryChain",
    "ClassificationRule",
    "ElementClassifier",
    "AttrMatcher",
    "get_classifier",
    "INTERACTIVE_TYPES",
    "SupescrapperError",
    "FetchError",
    "SelectorError",
    "ExportError",
    "DynamicPageError",
]
