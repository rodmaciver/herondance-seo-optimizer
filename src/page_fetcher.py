"""Fetch and parse a live Squarespace page into a PageSnapshot."""
from __future__ import annotations

from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from .schema import PageSnapshot
from .workbook import normalize_url

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_SECONDS = 15
MAX_BODY_WORDS = 6000


def _load_legacy_sections() -> list[str]:
    with open(CONFIG_DIR / "standard_sections.yaml") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("legacy_sections_to_remove", [])


def _extract_main_content(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup.select("script, style, nav, footer, header"):
        tag.decompose()
    main = soup.select_one("main") or soup.select_one("#page") or soup.body
    return main or soup


def _detect_legacy_sections(headings: list[str], body_text: str) -> list[str]:
    legacy_sections = _load_legacy_sections()
    found = []
    lowered_headings = [h.lower() for h in headings]
    for section in legacy_sections:
        needle = section.lower()
        if any(needle in h for h in lowered_headings):
            anchor = next((h for h in headings if needle in h.lower()), section)
            found.append(f"{section} (near: {anchor!r})")
    return found


def fetch_page(url: str) -> PageSnapshot:
    """Fetch a live page and parse it into a PageSnapshot.

    Normalizes the URL before fetching so double-slash, trailing-slash, and
    tracking-param variants all resolve to the same canonical form.
    Network/parse errors are returned as a PageSnapshot with `error` set,
    rather than raised, so the UI can surface a friendly message.
    """
    url = normalize_url(url) or url
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return PageSnapshot(url=url, error=f"Couldn't fetch this page — {exc}")

    if resp.status_code != 200:
        return PageSnapshot(
            url=url,
            error=f"Couldn't fetch this page — check the URL is published (HTTP {resp.status_code}).",
        )

    soup = BeautifulSoup(resp.text, "html.parser")
    main = _extract_main_content(soup)

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    meta_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = meta_tag.get("content", "").strip() if meta_tag else None

    h1_tag = main.find("h1") or soup.find("h1")
    h1 = h1_tag.get_text(strip=True) if h1_tag else None

    headings = []
    for tag in main.find_all(["h1", "h2", "h3", "h4"]):
        text = tag.get_text(strip=True)
        if text:
            headings.append(f"{tag.name.upper()}: {text}")

    body_text = main.get_text(separator="\n", strip=True)
    if not body_text:
        return PageSnapshot(url=url, error="Couldn't fetch this page — page body is empty.")

    words = body_text.split()
    truncated = len(words) > MAX_BODY_WORDS
    if truncated:
        body_text = " ".join(words[:MAX_BODY_WORDS])

    legacy_sections_found = _detect_legacy_sections(headings, body_text)

    return PageSnapshot(
        url=url,
        title=title,
        meta_description=meta_description,
        h1=h1,
        headings=headings,
        body_text=body_text,
        truncated=truncated,
        legacy_sections_found=legacy_sections_found,
    )
