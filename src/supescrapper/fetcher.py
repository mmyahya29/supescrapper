from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import DynamicPageError, FetchError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _normalize_url(url: str) -> str:
    """Normalize URL by adding https:// if no scheme is present."""
    url = url.strip()
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    return url


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
) -> str:
    """Fetch *url* with requests and return raw HTML."""
    url = _normalize_url(url)
    session = _build_session(timeout=timeout, retries=retries, user_agent=user_agent, proxies=proxies)
    if headers:
        session.headers.update(headers)
    try:
        resp = session.get(url, timeout=timeout, cookies=cookies)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Failed to fetch {url}: {exc}") from exc
    return resp.text


def fetch_dynamic(
    url: str,
    *,
    timeout: int = 30_000,
    wait_until: str = "domcontentloaded",
    user_agent: str = _USER_AGENT,
    viewport: dict[str, int] | None = None,
) -> str:
    """Fetch *url* with Playwright (headless Chromium) and return rendered HTML."""
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
        ctx = browser.new_context(
            user_agent=user_agent,
            viewport=viewport or {"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as exc:
            raise FetchError(f"Failed to load {url} with Playwright: {exc}") from exc
        html = page.content()
        browser.close()
    return html
