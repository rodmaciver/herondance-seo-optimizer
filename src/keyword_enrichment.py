"""Stage between candidate generation and judge synthesis: pools the keyword
candidates proposed by every generator, grounds them in real search data via
DataForSEO (single bulk request per metric), and widens the pool with
related/suggested terms.

Strictly additive: with no DataForSEO credentials, or if any DataForSEO call
fails, this returns the seed pool unchanged with has_data=False everywhere
and a status the UI can show -- it never raises.
"""
from __future__ import annotations

from . import dataforseo_client as dfs
from .schema import CandidateSet


def pool_seed_keywords(candidates: list[CandidateSet]) -> list[str]:
    """Dedupe (case-insensitive) keywords proposed across all generator candidates."""
    seen: dict[str, str] = {}
    for c in candidates:
        for kw in c.keywords:
            kw = kw.strip()
            if kw and kw.lower() not in seen:
                seen[kw.lower()] = kw
    return list(seen.values())


def enrich(seed_keywords: list[str], cfg: dict) -> tuple[list[dict], dict]:
    """Ground and widen the seed keyword pool.

    Returns (pool, status).
      pool: list of {"term", "volume", "difficulty", "has_data"}
      status: {"connected": bool, "error": str | None}

    Each DataForSEO call is independent -- a failure in one (e.g. Labs 403)
    does not block the others. Set enable_labs: false in dataforseo.yaml if
    your DataForSEO plan does not include Labs endpoints.
    """
    pool: dict[str, dict] = {
        kw.lower(): {"term": kw, "volume": None, "difficulty": None, "has_data": False} for kw in seed_keywords
    }

    if not dfs.credentials_available():
        return list(pool.values()), {"connected": False, "error": None}

    mode = cfg.get("mode", "sandbox")
    locale = cfg.get("locale", {})
    location_code = locale.get("location_code", 2840)
    language_code = locale.get("language_code", "en")
    enable_labs = cfg.get("enable_labs", False)

    warnings: list[str] = []

    # --- Labs: related keywords (widens pool) ---
    if enable_labs:
        try:
            related_limit = cfg.get("related_keywords_per_seed", 10)
            max_seeds = cfg.get("max_seed_keywords_for_related", 15)
            related = dfs.related_keywords(
                seed_keywords[:max_seeds], location_code, language_code, limit=related_limit, mode=mode
            )
            for term in related:
                pool.setdefault(term.lower(), {"term": term, "volume": None, "difficulty": None, "has_data": False})
        except Exception as exc:
            warnings.append(f"related_keywords skipped: {exc}")

    all_terms = [entry["term"] for entry in pool.values()]

    # --- Google Ads: search volume (available on all plans) ---
    volumes: dict = {}
    try:
        volumes = dfs.search_volume(all_terms, location_code, language_code, mode=mode)
    except Exception as exc:
        warnings.append(f"search_volume failed: {exc}")

    # --- Labs: keyword difficulty ---
    difficulties: dict = {}
    if enable_labs:
        try:
            difficulties = dfs.keyword_difficulty(all_terms, location_code, language_code, mode=mode)
        except Exception as exc:
            warnings.append(f"keyword_difficulty skipped: {exc}")

    # Merge data back into pool.
    any_data = False
    for key, entry in pool.items():
        vol = volumes.get(key)
        diff = difficulties.get(key)
        if vol is not None or diff is not None:
            entry["volume"] = vol
            entry["difficulty"] = diff
            entry["has_data"] = True
            any_data = True

    if not any_data and warnings:
        return list(pool.values()), {"connected": False, "error": "; ".join(warnings)}

    warning_str = "; ".join(warnings) if warnings else None
    return list(pool.values()), {"connected": True, "error": warning_str}


def score_viability(entry: dict, thresholds: dict) -> tuple[int, str]:
    """Deterministic 1-5 viability score + tier label, from config thresholds."""
    if not entry.get("has_data"):
        return 3, "no_data"
    difficulty = entry.get("difficulty")
    volume = entry.get("volume") or 0
    if difficulty is None:
        # Volume known but difficulty not available (Labs not enabled).
        # Score on volume alone as a rough proxy.
        return (4 if volume >= thresholds.get("min_volume", 10) else 3), "volume_only"
    if difficulty <= thresholds["difficulty_low_max"]:
        return (5 if volume >= thresholds["min_volume"] else 4), "winnable"
    if difficulty <= thresholds["difficulty_medium_max"]:
        return 3, "reach"
    return 1, "long_shot"


def category_label(tier: str, on_voice: bool, categories: dict) -> str:
    suffix = "on_voice" if on_voice else "off_voice"
    return categories.get(f"{tier}_{suffix}", f"{tier} ({'on' if on_voice else 'off'}-voice)")
