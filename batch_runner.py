"""Batch runner: process every URL in 'URLs to Do' and generate paste-pack .docx files.

Usage:
    python batch_runner.py                  # all URLs
    python batch_runner.py --limit 5        # first 5 URLs only (for testing)

Output:
    output/<slug>_<date>.docx  — one per URL, also uploaded to Shared Drive
    output/batch_errors.txt    — URLs that failed with reasons
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _plan_decision(plan, field: str) -> str:
    """Return the plan's decision for a given field ('' if absent)."""
    for item in plan.items:
        if item.field == field:
            return item.decision or ""
    return ""


def _ledger_new_url(url: str, plan) -> str:
    """Live URL after the change: redirect target if the slug changed, else unchanged."""
    if plan.redirect_mapping:
        import re as _re
        m = _re.match(r"^.*->\s*(\S+)\s+301$", plan.redirect_mapping)
        if m:
            m2 = _re.match(r"^(https?://[^/]+)", url)
            if m2:
                return m2.group(1) + m.group(1)
    return url


def _ads_strength(plan) -> int:
    """Estimated page strength for ad ordering: total monthly search volume
    of the keywords this page's ads target (judge-selected keywords with
    real DataForSEO volume). Pages whose keywords nobody searches for
    score 0 and sink to the bottom of the ads spreadsheet."""
    pool = getattr(plan, "keyword_pool", None) or []
    score = sum((kw.volume or 0) for kw in pool if getattr(kw, "selected", False))
    if score == 0:
        # No selected keyword had volume data: fall back to the three
        # highest-volume keywords in the pool so ordering degrades gracefully.
        volumes = sorted(((kw.volume or 0) for kw in pool), reverse=True)
        score = sum(volumes[:3])
    return score


def _ledger_row(url: str, snapshot, plan) -> list:
    from datetime import datetime as _dt
    return [
        f"{_dt.now():%Y-%m-%d}",
        url,
        _ledger_new_url(url, plan),
        snapshot.title or "",
        _plan_decision(plan, "seo_title"),
        snapshot.meta_description or "",
        _plan_decision(plan, "meta_description"),
        plan.primary_keyword or "",
        "",  # implemented_date — VA fills in when live on Squarespace
    ]


def main(limit: int | None = None) -> None:
    from src import sheets_client, workbook
    from src.generators import get_runtime_config
    from src.google_ads import generate_ad_assets
    from src.ads_spreadsheet import create_ads_editor_csv, validate_ads_batch
    from src.pastepack import render_pastepack
    from src.pipeline import run_page

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    batch_ts = datetime.now()
    error_log_path = OUTPUT_DIR / f"batch_errors_{batch_ts:%Y%m%d_%H%M%S}.txt"
    errors: list[str] = []
    ads_batch: list[dict] = []

    # Load URL queue.
    xlsx_bytes: bytes | None = None
    if sheets_client.available():
        log.info("Reading queue from Google Sheets…")
        raw, xlsx_bytes = sheets_client.read_queue_with_bytes()
        priority_df, backlog_df = workbook.parse_queue_raw(raw)
    else:
        data_path = Path(__file__).resolve().parent / "data"
        xlsx_files = list(data_path.glob("*.xlsx"))
        if not xlsx_files:
            log.error("No Google Sheets credentials and no xlsx in data/. Exiting.")
            sys.exit(1)
        log.info("Reading queue from %s…", xlsx_files[0].name)
        priority_df, backlog_df = workbook.load_queue(xlsx_files[0])

    import pandas as pd
    all_rows = pd.concat([priority_df, backlog_df], ignore_index=True)
    all_rows = all_rows[all_rows["url"].str.startswith("http")]

    if "generated" in all_rows.columns:
        already_done = int(all_rows["generated"].astype(bool).sum())
        pending = all_rows[~all_rows["generated"].astype(bool)]
    else:
        already_done = 0
        pending = all_rows

    if limit:
        pending = pending.head(limit)

    excel_col = pending["_excel_row"].tolist() if "_excel_row" in pending.columns else [None] * len(pending)
    urls_to_process = list(zip(pending["url"].tolist(), excel_col))
    total = len(urls_to_process)
    log.info("Found %d URLs to process (%d already Generated, skipped).", total, already_done)

    runtime = get_runtime_config()
    if runtime.get("warning"):
        log.warning(runtime["warning"])

    generator_specs = runtime["generators"]
    judges = runtime.get("judges", [])
    if not judges:
        log.error("No judge model available — check models.yaml and API keys.")
        sys.exit(1)
    judge_id = judges[0]["model_config_id"]

    for idx, (url, excel_row) in enumerate(urls_to_process, 1):
        log.info("[%d/%d] %s", idx, total, url)

        try:
            snapshot, candidates, plan, brand_flags, kw_status = run_page(
                url, generator_specs, judge_id
            )
        except Exception as exc:
            msg = f"[{url}] SEO pipeline failed: {exc}"
            log.error(msg)
            errors.append(msg)
            continue

        try:
            ad_assets = generate_ad_assets(snapshot, plan)
            if ad_assets["flagged"]:
                msg = f"[{url}] Google Ads validation failed: {ad_assets['flag_reason']}"
                log.error(msg)
                errors.append(msg)
                continue
        except Exception as exc:
            msg = f"[{url}] Google Ads generation failed: {exc}"
            log.error(msg)
            errors.append(msg)
            continue

        ads_entry = {
            "url": url,
            # Pin ads to the LIVE URL from the queue, not the SEO plan's
            # proposed slug. If the plan proposes a URL change that is never
            # applied in Squarespace, ads pointing at the proposed slug would
            # land on a page that does not exist. The live URL always works —
            # and if a redirect IS later applied, the 301 still carries the
            # click to the new page.
            "ads_final_url": url.rstrip("/"),
            "flagged": ad_assets.get("flagged", False),
            "flag_reason": ad_assets.get("flag_reason", ""),
            "headlines": ad_assets.get("headlines", []),
            "descriptions": ad_assets.get("descriptions", []),
            "core_keywords": ad_assets.get("core_keywords", []),
            "keyword_variants": ad_assets.get("keyword_variants", {}),
            "negative_keywords": ad_assets.get("negative_keywords", []),
            "strength_score": _ads_strength(plan),
        }
        try:
            validate_ads_batch(ads_batch + [ads_entry])
        except Exception as exc:
            msg = f"[{url}] Google Ads export validation failed: {exc}"
            log.error(msg)
            errors.append(msg)
            continue

        try:
            result = render_pastepack(plan, snapshot, operator="admin", ad_assets=ad_assets)
            log.info("  Wrote %s", Path(result["docx_path"]).name)
        except Exception as exc:
            msg = f"[{url}] Paste-pack render failed: {exc}"
            log.error(msg)
            errors.append(msg)
            continue

        # Collect Google Ads data for the batch spreadsheet.
        ads_batch.append(ads_entry)

        # Mark as Generated in column B; write redirect mapping to column C (if any).
        # Read-modify-write: re-download the CURRENT sheet, apply only this
        # page's changes, and upload. Holding one snapshot for the whole run
        # meant any manual edit made while a batch was running (e.g. clearing
        # Generated markers to force a rerun) was silently overwritten by the
        # next per-page upload.
        if sheets_client.available() and excel_row is not None:
            try:
                _, fresh_bytes = sheets_client.read_queue_with_bytes()
                fresh_bytes = sheets_client.update_queue_cell(fresh_bytes, excel_row, 2, "Generated")
                if plan.redirect_mapping:
                    fresh_bytes = sheets_client.update_queue_cell(
                        fresh_bytes, excel_row, 3, plan.redirect_mapping
                    )
                try:
                    fresh_bytes = sheets_client.append_ledger_row(
                        fresh_bytes, _ledger_row(url, snapshot, plan)
                    )
                except Exception as exc:
                    log.warning("  Ledger append failed (non-fatal): %s", exc)
                sheets_client.upload_queue(fresh_bytes)
                log.info("  Marked row %d as Generated in sheet.", excel_row)
            except Exception as exc:
                log.warning("  Failed to mark Generated in sheet: %s", exc)

        if sheets_client.available():
            try:
                docx_filename = Path(result["docx_path"]).name
                drive_url = sheets_client.upload_docx(result["docx_path"], docx_filename)
                log.info("  Uploaded to Drive: %s", drive_url)
            except Exception as exc:
                log.warning("  Drive upload failed: %s", exc)

    log.info("Done. %d/%d succeeded, %d errors.", total - len(errors), total, len(errors))

    # Build and upload one Google Ads Editor CSV for this batch.
    if ads_batch:
        try:
            # Strongest pages first so the top of the Ads Editor spreadsheet
            # is the natural "enable these first" shortlist.
            ads_batch.sort(key=lambda e: e.get("strength_score", 0), reverse=True)
            ss_path = create_ads_editor_csv(ads_batch, batch_ts)
            ss_name = Path(ss_path).name
            log.info("Google Ads Editor CSV: %s", ss_name)
            if sheets_client.available():
                try:
                    drive_url = sheets_client.upload_file(ss_path, ss_name)
                    log.info("Uploaded Google Ads Editor CSV: %s", drive_url)
                except Exception as exc:
                    log.warning("Failed to upload Google Ads Editor CSV: %s", exc)
        except Exception as exc:
            log.warning("Failed to create Google Ads Editor CSV: %s", exc)

    if errors:
        error_log_path.write_text("\n".join(errors))
        log.info("Error log: %s", error_log_path)
        if sheets_client.available():
            try:
                sheets_client.upload_file(str(error_log_path), error_log_path.name)
                log.info("Error log uploaded to Drive: %s", error_log_path.name)
            except Exception as exc:
                log.warning("Failed to upload error log to Drive: %s", exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch SEO + Google Ads paste-pack generator")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N URLs")
    args = parser.parse_args()
    main(limit=args.limit)
