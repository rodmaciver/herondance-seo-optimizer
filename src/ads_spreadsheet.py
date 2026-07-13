"""Google Ads Editor CSV export: one import-ready file per batch run."""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

DEFAULT_CAMPAIGN_NAME = "Heron Dance - Zen and Tao"
DEFAULT_MAX_CPC = "0.20"
DEFAULT_CAMPAIGN_BUDGET = "10.0"
DEFAULT_CAMPAIGN_LOCATION = "United States"
VALID_VALUETRACK_TOKENS = {"keyword", "matchtype"}
MAX_HEADLINES = 12
MAX_DESCRIPTIONS = 4
KEYWORD_STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
logger = logging.getLogger(__name__)

HEADERS = (
    [
        "Campaign", "Campaign Type", "Networks", "Budget", "Budget type",
        "Bid Strategy Type", "Campaign Status", "Location", "Ad Group",
        "Ad Group Status", "Max CPC", "Ad type", "Status", "Final URL",
        "Final URL suffix",
    ]
    + [f"Headline {i}" for i in range(1, MAX_HEADLINES + 1)]
    + [f"Description {i}" for i in range(1, MAX_DESCRIPTIONS + 1)]
    + ["Keyword", "Criterion Type"]
)


def _clean_keyword(value: str) -> str:
    """Remove match-type punctuation; the Criterion Type column owns match type."""
    return str(value).strip().strip("[]\"").strip()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _clean_keyword(value).casefold())


def _meaningful_tokens(value: str) -> list[str]:
    return [token for token in _tokens(value) if token not in KEYWORD_STOPWORDS]


def _word_order_key(value: str) -> tuple[str, ...]:
    return tuple(sorted(set(_meaningful_tokens(value))))


def _contains_token_sequence(longer: str, shorter: str) -> bool:
    longer_tokens = _meaningful_tokens(longer)
    shorter_tokens = _meaningful_tokens(shorter)
    if not shorter_tokens or len(shorter_tokens) >= len(longer_tokens):
        return False
    span = len(shorter_tokens)
    return any(longer_tokens[i:i + span] == shorter_tokens for i in range(len(longer_tokens) - span + 1))


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_keyword(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _dedupe_phrase_keywords(values: list[str]) -> list[str]:
    """Deduplicate phrase-match keywords using client-specified close-variant rules."""
    first_pass: list[str] = []
    seen_word_order: set[tuple[str, ...]] = set()
    for value in values:
        cleaned = _clean_keyword(value)
        key = _word_order_key(cleaned)
        if cleaned and key and key not in seen_word_order:
            seen_word_order.add(key)
            first_pass.append(cleaned)

    result: list[str] = []
    for candidate in first_pass:
        contained_existing_index = next(
            (
                index
                for index, existing in enumerate(result)
                if _contains_token_sequence(existing, candidate)
            ),
            None,
        )
        if contained_existing_index is not None:
            result[contained_existing_index] = candidate
            continue
        if any(_contains_token_sequence(candidate, existing) for existing in result):
            continue
        result.append(candidate)
    return result


def _normalize_final_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid Google Ads final URL: {value!r}")
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", parsed.netloc, path, parsed.query, ""))


def _ad_group_from_url(final_url: str) -> str:
    path = urlsplit(final_url).path.strip("/")
    raw = path.rsplit("/", 1)[-1] if path else "home"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if not slug:
        raise ValueError(f"Could not derive ad group from final URL: {final_url}")
    return slug


def _tracking_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _final_url_suffix(ad_group: str, campaign_name: str) -> str:
    return (
        "utm_source=google&utm_medium=cpc"
        f"&utm_campaign={_tracking_token(campaign_name)}"
        f"&utm_content={_tracking_token(ad_group)}"
        "&utm_term={keyword}&matchtype={matchtype}"
    )


def _blank_row() -> dict[str, str]:
    return {header: "" for header in HEADERS}


def _positive_keywords(entry: dict) -> list[str]:
    values: list[str] = []
    variants = entry.get("keyword_variants", {})
    for core in entry.get("core_keywords", []):
        cleaned_core = _clean_keyword(core)
        values.append(cleaned_core)
        values.extend(variants.get(core, variants.get(cleaned_core, [])))
    return _dedupe_phrase_keywords(values)


def _entry_max_cpc(entry: dict, default_max_cpc: str) -> str:
    return str(entry.get("max_cpc") or default_max_cpc).strip()


def _keyword_max_cpc(entry: dict, keyword: str, default_max_cpc: str) -> str:
    overrides = entry.get("keyword_max_cpc") or entry.get("keyword_bids") or {}
    if not isinstance(overrides, dict):
        return default_max_cpc
    cleaned = _clean_keyword(keyword)
    value = overrides.get(keyword, overrides.get(cleaned, overrides.get(cleaned.casefold())))
    return str(value or default_max_cpc).strip()


def _validate_final_url_suffix(suffix: str) -> None:
    tokens = set(re.findall(r"{([^{}]+)}", suffix))
    invalid = tokens - VALID_VALUETRACK_TOKENS
    if invalid:
        raise ValueError(
            "Final URL suffix contains unsupported ValueTrack token(s): "
            + ", ".join(sorted(invalid))
        )


def _safe_cell(value: str) -> str:
    text = str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def validate_ads_entry(entry: dict) -> None:
    """Reject data that would create missing or unsafe Google Ads rows."""
    if entry.get("flagged"):
        reason = entry.get("flag_reason") or "Google Ads validation failed"
        raise ValueError(str(reason))

    final_url = _normalize_final_url(entry.get("ads_final_url") or entry.get("url", ""))
    _ad_group_from_url(final_url)

    headlines = [str(v).strip() for v in entry.get("headlines", []) if str(v).strip()]
    descriptions = [str(v).strip() for v in entry.get("descriptions", []) if str(v).strip()]
    core_keywords = entry.get("core_keywords", [])
    variants_map = entry.get("keyword_variants", {})
    positives = _positive_keywords(entry)
    negatives = _dedupe(entry.get("negative_keywords", []))

    if not (3 <= len(headlines) <= 12):
        raise ValueError(f"Need 3-12 headlines, got {len(headlines)}")
    if any(len(value) > 30 for value in headlines):
        raise ValueError("At least one headline exceeds 30 characters")
    if not (2 <= len(descriptions) <= 4):
        raise ValueError(f"Need 2-4 descriptions, got {len(descriptions)}")
    if any(len(value) > 90 for value in descriptions):
        raise ValueError("At least one description exceeds 90 characters")
    if not (3 <= len(core_keywords) <= 8):
        raise ValueError(f"Need 3-8 core keywords, got {len(core_keywords)}")
    for core in core_keywords:
        cleaned = _clean_keyword(core)
        variants = variants_map.get(core, variants_map.get(cleaned, []))
        if not (2 <= len(variants) <= 4):
            raise ValueError(
                f"Core keyword {core!r} needs 2-4 variants, got {len(variants)}"
            )
    if not positives:
        raise ValueError("No positive keywords were generated")
    if len(negatives) > 10:
        raise ValueError(f"Need 0-10 page-specific negative keywords, got {len(negatives)}")
    overlap = {value.casefold() for value in positives} & {
        value.casefold() for value in negatives
    }
    if overlap:
        raise ValueError(
            "Keywords cannot be both positive and negative: " + ", ".join(sorted(overlap))
        )


def validate_ads_batch(batch_results: list[dict]) -> None:
    """Validate every entry and reject ambiguous ad-group identities."""
    ad_group_urls: dict[str, str] = {}
    seen_keywords: dict[tuple[str, ...], str] = {}
    for entry in batch_results:
        validate_ads_entry(entry)
        final_url = _normalize_final_url(entry.get("ads_final_url") or entry.get("url", ""))
        ad_group = _ad_group_from_url(final_url)
        previous_url = ad_group_urls.get(ad_group)
        if previous_url and previous_url != final_url:
            raise ValueError(
                f"Ad group {ad_group!r} maps to multiple final URLs: "
                f"{previous_url!r} and {final_url!r}"
            )
        ad_group_urls[ad_group] = final_url
        for keyword in _positive_keywords(entry):
            key = _word_order_key(keyword)
            previous_ad_group = seen_keywords.get(key)
            if previous_ad_group and previous_ad_group != ad_group:
                logger.warning(
                    "Skipping duplicate phrase-match keyword across ad groups: %r "
                    "already assigned to %s; duplicate found in %s",
                    keyword,
                    previous_ad_group,
                    ad_group,
                )
                continue
            seen_keywords[key] = ad_group


def _campaign_row(
    campaign_name: str,
    campaign_budget: str,
    campaign_location: str,
) -> dict[str, str]:
    row = _blank_row()
    row.update({
        "Campaign": campaign_name,
        "Campaign Type": "Search",
        "Networks": "Google search",
        "Budget": str(campaign_budget),
        "Budget type": "Daily",
        "Bid Strategy Type": "Manual CPC",
        "Campaign Status": "Paused",
        "Location": campaign_location,
    })
    return row


def _rows_for_entry(
    entry: dict,
    *,
    campaign_name: str,
    default_max_cpc: str,
    assigned_keyword_keys: set[tuple[str, ...]],
) -> list[dict[str, str]]:
    final_url = _normalize_final_url(entry.get("ads_final_url") or entry["url"])
    ad_group = _ad_group_from_url(final_url)
    suffix = _final_url_suffix(ad_group, campaign_name)
    _validate_final_url_suffix(suffix)
    max_cpc = _entry_max_cpc(entry, default_max_cpc)
    headlines = [str(v).strip() for v in entry.get("headlines", []) if str(v).strip()]
    descriptions = [str(v).strip() for v in entry.get("descriptions", []) if str(v).strip()]

    rows: list[dict[str, str]] = []

    ad_group_row = _blank_row()
    ad_group_row.update({
        "Campaign": campaign_name,
        "Ad Group": ad_group,
        "Ad Group Status": "Paused",
        "Max CPC": max_cpc,
    })
    rows.append(ad_group_row)

    ad_row = _blank_row()
    ad_row.update({
        "Campaign": campaign_name,
        "Ad Group": ad_group,
        "Ad type": "Responsive search ad",
        "Status": "Paused",
        "Final URL": final_url,
        "Final URL suffix": suffix,
    })
    for index, headline in enumerate(headlines, 1):
        ad_row[f"Headline {index}"] = headline
    for index, description in enumerate(descriptions, 1):
        ad_row[f"Description {index}"] = description
    rows.append(ad_row)

    for keyword in _positive_keywords(entry):
        key = _word_order_key(keyword)
        if key in assigned_keyword_keys:
            logger.warning(
                "Omitting cross-ad-group duplicate phrase-match keyword from %s: %r",
                ad_group,
                keyword,
            )
            continue
        assigned_keyword_keys.add(key)
        row = _blank_row()
        row.update({
            "Campaign": campaign_name,
            "Ad Group": ad_group,
            "Max CPC": _keyword_max_cpc(entry, keyword, max_cpc),
            "Status": "Paused",
            "Keyword": keyword,
            "Criterion Type": "Phrase",
        })
        rows.append(row)

    for keyword in _dedupe(entry.get("negative_keywords", [])):
        row = _blank_row()
        row.update({
            "Campaign": campaign_name,
            "Ad Group": ad_group,
            "Keyword": keyword,
            "Criterion Type": "Negative phrase",
        })
        rows.append(row)

    return rows


def _validate_output_rows(rows: list[dict[str, str]]) -> None:
    seen_keyword_keys: set[tuple[str, ...]] = set()
    defined_campaigns = {
        row["Campaign"] for row in rows if row["Campaign Type"]
    }
    for index, row in enumerate(rows, start=2):
        if row["Ad type"]:
            headlines = [
                row[f"Headline {i}"]
                for i in range(1, MAX_HEADLINES + 1)
                if row[f"Headline {i}"]
            ]
            descriptions = [
                row[f"Description {i}"]
                for i in range(1, MAX_DESCRIPTIONS + 1)
                if row[f"Description {i}"]
            ]
            if row["Ad type"] != "Responsive search ad":
                raise ValueError(f"Row {index}: invalid Ad type {row['Ad type']!r}")
            if not row["Final URL"]:
                raise ValueError(f"Row {index}: ad row is missing Final URL")
            if not headlines:
                raise ValueError(f"Row {index}: ad row is missing headlines")
            if len(headlines) < 3 or len(descriptions) < 2:
                raise ValueError(f"Row {index}: ad row has too few headlines/descriptions")
            _validate_final_url_suffix(row["Final URL suffix"])

        if row["Keyword"]:
            if not row["Criterion Type"]:
                raise ValueError(f"Row {index}: keyword row is missing Criterion Type")
            if row["Criterion Type"] == "Phrase":
                if row["Final URL"] or row["Final URL suffix"]:
                    raise ValueError(f"Row {index}: keyword row must not include URL fields")
                key = _word_order_key(row["Keyword"])
                if key in seen_keyword_keys:
                    raise ValueError(f"Row {index}: duplicate phrase keyword {row['Keyword']!r}")
                seen_keyword_keys.add(key)

        if defined_campaigns and row["Campaign"] not in defined_campaigns:
            raise ValueError(f"Row {index}: campaign is not defined in this file")


def _safe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{key: _safe_cell(value) for key, value in row.items()} for row in rows]


def create_ads_editor_csv(
    batch_results: list[dict],
    timestamp: datetime,
    *,
    campaign_name: str = DEFAULT_CAMPAIGN_NAME,
    max_cpc: str = DEFAULT_MAX_CPC,
    campaign_budget: str = DEFAULT_CAMPAIGN_BUDGET,
    campaign_location: str = DEFAULT_CAMPAIGN_LOCATION,
    create_campaign: bool = True,
) -> str:
    """Build one Google Ads Editor CSV for all successful URLs in a batch."""
    if not batch_results:
        raise ValueError("Cannot create a Google Ads CSV for an empty batch")

    validate_ads_batch(batch_results)
    rows: list[dict[str, str]] = []
    if create_campaign:
        rows.append(_campaign_row(campaign_name, campaign_budget, campaign_location))

    assigned_keyword_keys: set[tuple[str, ...]] = set()
    for entry in batch_results:
        rows.extend(
            _rows_for_entry(
                entry,
                campaign_name=campaign_name,
                default_max_cpc=max_cpc,
                assigned_keyword_keys=assigned_keyword_keys,
            )
        )

    _validate_output_rows(rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"Google Ads Editor Upload {timestamp:%Y%m%d_%H%M%S}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(_safe_rows(rows))
    return str(path)
