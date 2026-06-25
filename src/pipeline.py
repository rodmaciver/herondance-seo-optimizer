"""Pure orchestration: fetch -> generate -> judge -> verify. No Gradio imports.

Runnable headless: `python src/pipeline.py <url>`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

from .generators import generate_candidates, get_runtime_config
from .judge import synthesize, verify_brand_fit
from .keyword_enrichment import enrich, pool_seed_keywords
from .page_fetcher import fetch_page
from .schema import BrandFlag, CandidateSet, ExecutionPlan, PageSnapshot

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def run_page(
    url: str,
    generator_specs: list[dict],
    judge_model_id: str,
    gsc_row: dict | None = None,
    query_df: pd.DataFrame | None = None,
) -> tuple[PageSnapshot, list[CandidateSet], ExecutionPlan, list[BrandFlag], dict]:
    """Run the full generate -> enrich -> judge -> brand-fit-verify pipeline for one page.

    Returns (snapshot, candidates, plan, brand_flags, keyword_status), where
    keyword_status is {"connected": bool, "error": str | None}.
    """
    brand = _load_yaml("brand_constitution.yaml")
    rubric = _load_yaml("seo_rubric.yaml")
    dataforseo_cfg = _load_yaml("dataforseo.yaml")

    snapshot = fetch_page(url)
    if snapshot.error:
        raise RuntimeError(snapshot.error)

    candidates = generate_candidates(snapshot, brand, rubric, gsc_row, query_df, generator_specs)

    seed_keywords = pool_seed_keywords(candidates)
    enriched_pool, keyword_status = enrich(seed_keywords, dataforseo_cfg)

    plan = synthesize(candidates, snapshot, brand, rubric, gsc_row, judge_model_id, enriched_pool, dataforseo_cfg)
    brand_flags = verify_brand_fit(plan, brand, judge_model_id)

    return snapshot, candidates, plan, brand_flags, keyword_status


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    if len(sys.argv) != 2:
        print("Usage: python -m src.pipeline <url>")
        sys.exit(1)

    target_url = sys.argv[1]
    runtime = get_runtime_config()
    if runtime.get("warning"):
        print(f"WARNING: {runtime['warning']}\n")

    judge_id = runtime["judges"][0]["model_config_id"]

    snapshot, candidates, plan, brand_flags, keyword_status = run_page(target_url, runtime["generators"], judge_id)

    print(f"=== Page snapshot: {snapshot.url} ===")
    print(f"Title: {snapshot.title}")
    print(f"H1: {snapshot.h1}")
    print(f"Legacy sections found: {snapshot.legacy_sections_found}")
    print()

    print("=== Candidates ===")
    for c in candidates:
        print(f"--- {c.model_label} ---")
        print(c.model_dump_json(indent=2))
    print()

    print("=== Execution plan ===")
    print(plan.model_dump_json(indent=2))
    print()

    print("=== Brand-fit verification ===")
    for flag in brand_flags:
        print(f"{flag.field}: {flag.status} - {flag.note}")
    print()

    if keyword_status["connected"]:
        print("=== Keyword enrichment: connected (DataForSEO) ===")
    elif keyword_status["error"]:
        print(f"=== Keyword enrichment FAILED, ran ungrounded: {keyword_status['error']} ===")
    else:
        print("=== Keyword enrichment: not connected (no DATAFORSEO_LOGIN/PASSWORD) ===")
    for kw in plan.keyword_pool:
        print(
            f"{'★' if kw.selected else ' '} {kw.term:<40} vol={kw.volume if kw.has_data else 'n/a':<8} "
            f"diff={kw.difficulty if kw.has_data else 'n/a':<6} voice={kw.voice_score} "
            f"viability={kw.viability_score} [{kw.category}] {kw.note}"
        )
