"""
supescrapper — Universal web DOM scraper.

Quick start:
    from supescrapper import Scraper

    results = Scraper().fetch("https://example.com").query("h1").extract()
    results.export("json", "output.json")
"""

from .scraper import Scraper
from .models import ScrapedItem, ScrapedResultSet
from .query import QueryChain
from .exceptions import (
    SupescrapperError,
    FetchError,
    SelectorError,
    ExportError,
    DynamicPageError,
)

__version__ = "0.1.0"

__all__ = [
    "Scraper",
    "ScrapedItem",
    "ScrapedResultSet",
    "QueryChain",
    "SupescrapperError",
    "FetchError",
    "SelectorError",
    "ExportError",
    "DynamicPageError",
]
