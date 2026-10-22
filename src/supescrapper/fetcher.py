from __future__ import annotations

import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import DynamicPageError, FetchError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def _normalize_url(url: str) -> str:
    """Normalize URL by adding https:// if no scheme:// is present."""
    url = url.strip()
    if not url:
        return url
    if _SCHEME_RE.match(url):
        return url
    if url.startswith("//"):
        # protocol-relative URL
        return "https:" + url
    # Scheme-less (e.g. "example.com", "example.com:8080/path")
    return "https://" + url


def _build_session(
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 0.5,
    user_agent: str = _USER_AGENT,
    proxies: dict[str, str] | None = None,
) -> requests.Session:
    session = requests.Session()
    retry = Retry(total=retries, backoff_factor=backoff, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": user_agent})
    if proxies:
        session.proxies.update(proxies)
    return session


def fetch_static(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    user_agent: str = _USER_AGENT,
    proxies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    session: requests.Session | None = None,
) -> str:
    """Fetch *url* with requests and return raw HTML."""
    url = _normalize_url(url)
    owns_session = session is None
    if session is None:
        session = _build_session(timeout=timeout, retries=retries, user_agent=user_agent, proxies=proxies)
    if headers:
        session.headers.update(headers)
    try:
        resp = session.get(url, timeout=timeout, cookies=cookies)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Failed to fetch {url}: {exc}") from exc
    finally:
        if owns_session:
            session.close()
    return resp.text


def fetch_dynamic(
    url: str,
    *,
    timeout: int = 30_000,
    wait_until: str = "domcontentloaded",
    user_agent: str = _USER_AGENT,
    viewport: dict[str, int] | None = None,
    scroll: int = 0,
    scroll_delay_ms: int = 1000,
    wait_for: str | None = None,
    wait_ms: int | None = None,
) -> str:
    """Fetch *url* with Playwright (headless Chromium) and return rendered HTML.

    scroll:        number of times to wheel-scroll down (triggers lazy loading)
    scroll_delay_ms: settle delay after each scroll step
    wait_for:      optional CSS selector to wait for before capturing HTML
    wait_ms:       optional fixed wait (ms) after navigation
    """
    url = _normalize_url(url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise DynamicPageError(
            "Playwright is required for dynamic pages. "
            "Install it: pip install supescrapper[dynamic] && playwright install"
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=user_agent,
                viewport=viewport or {"width": 1920, "height": 1080},
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until=wait_until, timeout=timeout)
                if wait_for:
                    page.wait_for_selector(wait_for, timeout=timeout)
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                for _ in range(max(0, scroll)):
                    page.mouse.wheel(0, 1000)
                    page.wait_for_timeout(scroll_delay_ms)
            except Exception as exc:
                raise FetchError(f"Failed to load {url} with Playwright: {exc}") from exc
            return page.content()
        finally:
            browser.close()
