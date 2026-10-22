class SupescrapperError(Exception):
    """Base exception for all supescrapper errors."""


class FetchError(SupescrapperError):
    """Raised when fetching a URL fails."""


class SelectorError(SupescrapperError):
    """Raised when a selector is invalid or returns no matches."""


class ExportError(SupescrapperError):
    """Raised when exporting results fails."""


class DynamicPageError(SupescrapperError):
    """Raised when Playwright is needed but not installed."""


class SchemaError(SupescrapperError):
    """Raised when a scraping schema is invalid or a required field is missing."""
