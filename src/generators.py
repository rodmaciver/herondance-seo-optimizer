"""Stage 1: candidate SEO recommendations from 2-3 frontier models in parallel."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

log = logging.getLogger(__name__)

from .model_clients import available_providers, call_model, get_model_config
from .schema import CandidateSet, PageSnapshot

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

GENERATOR_SYSTEM_PROMPT = """\
You are an SEO specialist working on a contemplative literary website.
You will be given the site's brand constitution, an SEO rubric, a snapshot
of one page, and that page's Google Search Console statistics.

Follow the brand constitution's guiding principle above all else: do not
chase traffic at the cost of the site's distinctive contemplative voice.
"keep current" is always an acceptable answer for any field.

Return your suggestions in the required structured format:
- h1_options: 1-2 candidate H1s (or the current H1 if it should stay)
- url_slug: a new slug, or the literal string "keep current"
- seo_title: <= 60 characters, "Primary phrase | Secondary phrase" style
- meta_description: <= 155 characters
- body_change: exactly ONE body-content change (revision, addition, heading
  change, or structural improvement), describing WHERE on the page it goes
  using a verbatim quote from the page as an anchor
- keywords: {keyword_count} candidate keywords/phrases this page could target.
  Cast a WIDE net based on semantic and literary fit with the page and brand
  voice -- ignore search volume and difficulty entirely at this stage (those
  will be checked separately against real search data). Include a mix of
  obvious and niche/long-tail phrasings.
- keep_current: list any of the above fields you recommend leaving unchanged
"""


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def get_runtime_config() -> dict:
    """Resolve which generator/judge models are usable given the available API keys.

    Returns a dict with `generators` (list of specs), `judges` (list of specs),
    and an optional `warning` string for degraded (single-vendor) mode.
    """
    providers = available_providers()
    models_cfg = _load_yaml("models.yaml")
    defaults = models_cfg["defaults"]

    def provider_available(model_cfg: dict) -> bool:
        provider = model_cfg["provider"]
        if provider == "anthropic":
            return providers["anthropic"]
        if provider == "openai":
            return providers["openai"]
        if provider == "openai_compatible":
            import os

            return bool(os.environ.get(model_cfg.get("api_key_env", "")))
        return False

    all_models = {m["id"]: m for m in models_cfg["models"]}
    available_ids = [mid for mid, m in all_models.items() if provider_available(m)]

    if len(available_ids) >= 2:
        generators = [
            {"id": mid, "label": all_models[mid]["label"], "model_config_id": mid, "temperature": 0.7}
            for mid in defaults["generators"]
            if mid in available_ids
        ]
        # Prefer non-generator models as judge so the UI default doesn't conflict.
        generator_ids = {g["model_config_id"] for g in generators}
        non_gen_ids = [mid for mid in available_ids if mid not in generator_ids]
        gen_ids = [mid for mid in available_ids if mid in generator_ids]
        default_judge = defaults["judge"]
        if default_judge in available_ids:
            rest = [j for j in non_gen_ids if j != default_judge] + [j for j in gen_ids if j != default_judge]
            judge_pool = [default_judge] + rest
        elif non_gen_ids:
            judge_pool = non_gen_ids + gen_ids
        else:
            judge_pool = gen_ids  # all available models are generators; allow overlap as last resort
        judges = [
            {"id": mid, "label": all_models[mid]["label"], "model_config_id": mid}
            for mid in dict.fromkeys(judge_pool)
        ]
        return {"generators": generators, "judges": judges, "all_generator_choices": [
            {"id": mid, "label": all_models[mid]["label"], "model_config_id": mid, "temperature": 0.7}
            for mid in available_ids
        ], "warning": None}

    if "claude" in available_ids:
        generators = [
            {"id": "claude_a", "label": "Claude A (temp 0.4)", "model_config_id": "claude", "temperature": 0.4},
            {"id": "claude_b", "label": "Claude B (temp 0.9)", "model_config_id": "claude", "temperature": 0.9},
        ]
        judges = [{"id": "claude_judge", "label": "Claude (Judge, temp 0.2)", "model_config_id": "claude", "temperature": 0.2}]
        return {
            "generators": generators,
            "judges": judges,
            "all_generator_choices": generators,
            "warning": (
                "Only ANTHROPIC_API_KEY is set. Running in degraded single-vendor "
                "mode with two Claude passes ('Claude A/B') instead of multiple "
                "vendors. Set OPENAI_API_KEY and/or GEMINI_API_KEY for the intended "
                "multi-vendor demo."
            ),
        }

    raise RuntimeError(
        "No API keys found. Set at least ANTHROPIC_API_KEY (see .env.example)."
    )


def _format_gsc_row(gsc_row: dict | None) -> str:
    if not gsc_row or not any(v is not None for v in gsc_row.values()):
        return "No Google Search Console data available for this page."
    clicks = gsc_row.get("clicks")
    impressions = gsc_row.get("impressions")
    position = gsc_row.get("avg_position")
    ctr = gsc_row.get("ctr")
    parts = []
    if clicks is not None and impressions is not None:
        parts.append(f"This page received {clicks:,.0f} clicks and {impressions:,.0f} impressions over the reporting period")
    if position is not None:
        parts.append(f"average search position {position:.1f}")
    if ctr is not None:
        parts.append(f"CTR {ctr:.2%}")
    return ", ".join(parts) + "." if parts else "No Google Search Console data available for this page."


def _format_queries(query_df: pd.DataFrame | None) -> str:
    if query_df is None or query_df.empty:
        return "Query-level search terms are not available; ground keyword suggestions in the page content and site theme."
    top = query_df.sort_values("impressions", ascending=False).head(25)
    lines = ["Top search queries for this page (query | impressions | clicks | position):"]
    for _, row in top.iterrows():
        lines.append(f"- {row['query']} | {row['impressions']:.0f} | {row['clicks']:.0f} | {row['position']:.1f}")
    return "\n".join(lines)


def build_generator_prompt(
    snapshot: PageSnapshot,
    brand: dict,
    rubric: dict,
    gsc_row: dict | None,
    query_df: pd.DataFrame | None,
) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    dataforseo_cfg = _load_yaml("dataforseo.yaml")
    keyword_count = dataforseo_cfg.get("keyword_candidates_per_generator", 12)
    system = (
        GENERATOR_SYSTEM_PROMPT.format(keyword_count=keyword_count)
        + "\n\nBrand constitution:\n"
        + yaml.dump(brand)
        + "\n\nSEO rubric:\n"
        + yaml.dump(rubric)
    )

    user_parts = [
        f"Page URL: {snapshot.url}",
        f"Current page title: {snapshot.title}",
        f"Current meta description: {snapshot.meta_description}",
        f"Current H1: {snapshot.h1}",
        "Current heading outline:\n" + "\n".join(snapshot.headings),
        "Page body text:\n" + snapshot.body_text,
        _format_gsc_row(gsc_row),
        _format_queries(query_df),
    ]
    if snapshot.legacy_sections_found:
        user_parts.append(
            "Note: this page contains legacy book-promo sections that will be "
            "removed and replaced automatically — do not spend your one body "
            "change on that; focus on SEO/content improvements."
        )
    return system, "\n\n".join(user_parts)


def _generate_one(
    spec: dict,
    snapshot: PageSnapshot,
    brand: dict,
    rubric: dict,
    gsc_row: dict | None,
    query_df: pd.DataFrame | None,
) -> CandidateSet:
    system, user = build_generator_prompt(snapshot, brand, rubric, gsc_row, query_df)
    result = call_model(
        spec["model_config_id"],
        system,
        user,
        CandidateSet,
        temperature=spec.get("temperature", 0.7),
    )
    result["model_label"] = spec["label"]
    return CandidateSet(**result)


def generate_candidates(
    snapshot: PageSnapshot,
    brand: dict,
    rubric: dict,
    gsc_row: dict | None,
    query_df: pd.DataFrame | None,
    generator_specs: list[dict],
) -> list[CandidateSet]:
    """Call each generator model in parallel and return their CandidateSets."""
    with ThreadPoolExecutor(max_workers=max(1, len(generator_specs))) as executor:
        futures = [
            (spec, executor.submit(_generate_one, spec, snapshot, brand, rubric, gsc_row, query_df))
            for spec in generator_specs
        ]
    results = []
    for spec, f in futures:
        try:
            results.append(f.result())
        except Exception as exc:
            log.warning("Generator %s failed, skipping: %s", spec["label"], exc)
    if not results:
        raise RuntimeError("All generators failed — cannot produce candidates.")
    return results
