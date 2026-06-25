"""Thin DataForSEO REST client -- Labs / Keywords Data endpoints only.

No SERP-position or backlink endpoints. Credentials come ONLY from the
DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD env vars (HTTP Basic auth) -- never
hardcode credentials, and never log them.
"""
from __future__ import annotations

import logging
import os

import requests

_log = logging.getLogger(__name__)

SANDBOX_BASE = "https://sandbox.dataforseo.com"
LIVE_BASE = "https://api.dataforseo.com"
TIMEOUT_SECONDS = 30


def credentials_available() -> bool:
    return bool(os.environ.get("DATAFORSEO_LOGIN")) and bool(os.environ.get("DATAFORSEO_PASSWORD"))


def _base_url(mode: str) -> str:
    return LIVE_BASE if mode == "live" else SANDBOX_BASE


def _auth() -> tuple[str, str]:
    return os.environ["DATAFORSEO_LOGIN"], os.environ["DATAFORSEO_PASSWORD"]


def _post(mode: str, path: str, payload: list[dict]) -> dict:
    resp = requests.post(_base_url(mode) + path, json=payload, auth=_auth(), timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    # Top-level failure = the whole request bombed (auth, malformed payload, etc.) — raise hard.
    top_code = data.get("status_code")
    if top_code and top_code != 20000:
        raise RuntimeError(f"DataForSEO error {top_code}: {data.get('status_message', 'unknown')}")
    # Task-level failure = couldn't fetch data for this specific batch (quota, no results, etc.)
    # Log and return partial data; callers treat missing items as has_data=False.
    tasks = data.get("tasks") or []
    if tasks:
        task_code = tasks[0].get("status_code")
        if task_code and task_code != 20000:
            _log.warning(
                "DataForSEO task warning %s: %s (partial results returned)",
                task_code,
                tasks[0].get("status_message", "unknown"),
            )
    return data


def search_volume(keywords: list[str], location_code: int, language_code: str, mode: str = "sandbox") -> dict[str, int | None]:
    """One bulk request: search volume for every keyword at once.

    Returns {lowercased keyword: search_volume or None}.
    """
    if not keywords:
        return {}
    payload = [{"keywords": keywords[:1000], "location_code": location_code, "language_code": language_code}]
    data = _post(mode, "/v3/keywords_data/google_ads/search_volume/live", payload)
    out: dict[str, int | None] = {}
    for task in data.get("tasks") or []:
        for item in task.get("result") or []:
            if item and item.get("keyword"):
                out[item["keyword"].lower()] = item.get("search_volume")
    return out


def ads_data(keywords: list[str], location_code: int, language_code: str, mode: str = "sandbox") -> dict[str, dict]:
    """One bulk request: search volume, CPC, and competition level for every keyword.

    Returns {lowercased keyword: {"volume": int|None, "cpc": float|None, "competition": str|None}}
    where competition is "LOW", "MEDIUM", "HIGH", or None.
    """
    if not keywords:
        return {}
    payload = [{"keywords": keywords[:1000], "location_code": location_code, "language_code": language_code}]
    data = _post(mode, "/v3/keywords_data/google_ads/search_volume/live", payload)
    out: dict[str, dict] = {}
    for task in data.get("tasks") or []:
        for item in task.get("result") or []:
            if item and item.get("keyword"):
                out[item["keyword"].lower()] = {
                    "volume": item.get("search_volume"),
                    "cpc": item.get("cpc"),
                    "competition": item.get("competition_level"),
                }
    return out


def keyword_difficulty(keywords: list[str], location_code: int, language_code: str, mode: str = "sandbox") -> dict[str, int | None]:
    """One bulk request: keyword difficulty for every keyword at once.

    Returns {lowercased keyword: keyword_difficulty or None}.
    """
    if not keywords:
        return {}
    payload = [{"keywords": keywords[:1000], "location_code": location_code, "language_code": language_code}]
    data = _post(mode, "/v3/dataforseo_labs/google/bulk_keyword_difficulty/live", payload)
    out: dict[str, int | None] = {}
    for task in data.get("tasks") or []:
        for item in task.get("result") or []:
            if item and item.get("keyword"):
                out[item["keyword"].lower()] = item.get("keyword_difficulty")
    return out


def related_keywords(
    seed_keywords: list[str],
    location_code: int,
    language_code: str,
    limit: int = 10,
    mode: str = "sandbox",
) -> set[str]:
    """One bulk request (one task per seed, submitted in a single POST):
    related/suggested terms to widen the keyword pool.
    """
    if not seed_keywords:
        return set()
    payload = [
        {"keyword": seed, "location_code": location_code, "language_code": language_code, "limit": limit}
        for seed in seed_keywords
    ]
    data = _post(mode, "/v3/dataforseo_labs/google/related_keywords/live", payload)
    terms: set[str] = set()
    for task in data.get("tasks") or []:
        for result in task.get("result") or []:
            for kw_item in (result or {}).get("items") or []:
                kw_data = kw_item.get("keyword_data") or {}
                term = kw_data.get("keyword")
                if term:
                    terms.add(term)
    return terms
