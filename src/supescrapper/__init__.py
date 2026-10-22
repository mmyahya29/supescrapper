"""
supescrapper — Universal web DOM scraper.

Quick start:
    from supescrapper import Scraper

    results = Scraper().fetch("https://example.com").query("h1").extract()
    results.export("json", "output.json")
"""

import logging as _logging

from .async_scraper import AsyncScraper
from .classifier import (
    INTERACTIVE_TYPES,
    AttrMatcher,
    ClassificationRule,
    ElementClassifier,
    get_classifier,
)
from .crawler import AsyncCrawlEngine, CrawlConfig, CrawlEngine, CrawlResult
from .exceptions import (
    DynamicPageError,
    ExportError,
    FetchError,
    SchemaError,
    SelectorError,
    SupescrapperError,
)
from .feeds import (
    ScrapeConfig,
    SourceConfig,
    async_scrape_feeds,
    load_config,
    run_config,
    scrape_feeds,
    scrape_sources,
)
from .impersonate import (
    DEFAULT_IMPERSONATE,
    AsyncImpersonatingFetcher,
    ImpersonateNotInstalledError,
    ImpersonatingFetcher,
    ProxyPool,
)
from .models import ScrapedItem, ScrapedResultSet
from .query import QueryChain
from .schema import (
    ARTICLE_SCHEMA,
    FEED_SCHEMA,
    NEWS_FEED_SCHEMA,
    NEWS_SCHEMA,
    PRODUCT_SCHEMA,
    RSS_SCHEMA,
    Field,
    Schema,
    SchemaResultSet,
    clean_text,
    extract_ld_json,
    from_ldjson,
    get_schema,
    list_schemas,
    load_schema_from_json,
    parse_datetime,
    register_schema,
    schema_from_dict,
    to_bool,
    to_float,
    to_int,
    truncate,
)
from .scraper import Scraper

_logging.getLogger("supescrapper").addHandler(_logging.NullHandler())

__version__ = "0.4.0"

__all__ = [
    "ARTICLE_SCHEMA",
    "DEFAULT_IMPERSONATE",
    "FEED_SCHEMA",
    "INTERACTIVE_TYPES",
    "NEWS_FEED_SCHEMA",
    "NEWS_SCHEMA",
    "PRODUCT_SCHEMA",
    "RSS_SCHEMA",
    "AsyncCrawlEngine",
    "AsyncImpersonatingFetcher",
    "AsyncScraper",
    "AttrMatcher",
    "ClassificationRule",
    "CrawlConfig",
    "CrawlEngine",
    "CrawlResult",
    "DynamicPageError",
    "ElementClassifier",
    "ExportError",
    "FetchError",
    "Field",
    "ImpersonateNotInstalledError",
    "ImpersonatingFetcher",
    "ProxyPool",
    "QueryChain",
    "Schema",
    "SchemaError",
    "SchemaResultSet",
    "ScrapeConfig",
    "ScrapedItem",
    "ScrapedResultSet",
    "Scraper",
    "SelectorError",
    "SourceConfig",
    "SupescrapperError",
    "async_scrape_feeds",
    "clean_text",
    "extract_ld_json",
    "from_ldjson",
    "get_classifier",
    "get_schema",
    "list_schemas",
    "load_config",
    "load_schema_from_json",
    "parse_datetime",
    "register_schema",
    "run_config",
    "schema_from_dict",
    "scrape_feeds",
    "scrape_sources",
    "to_bool",
    "to_float",
    "to_int",
    "truncate",
]
