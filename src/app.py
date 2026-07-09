"""Gradio UI — presentation layer only. All logic lives in src/*.py modules."""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

import gradio as gr
import pandas as pd
import yaml
from dotenv import load_dotenv

load_dotenv()

from . import sheets_client, workbook
from .generators import generate_candidates, get_runtime_config
from .judge import synthesize, verify_brand_fit
from .keyword_enrichment import enrich, pool_seed_keywords
from .page_fetcher import fetch_page
from .google_ads import generate_ad_assets
from .pastepack import render_pastepack
from .schema import BodyChange, CandidateSet, ExecutionPlan, PageSnapshot

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_PATH = BASE_DIR / "data" / "seo_workbook.xlsx"

MAX_GENERATORS = 3
MAX_PLAN_ITEMS = 4
MAX_BODY_CHANGES = 6


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def _short_path(url: str) -> str:
    path = urlparse(url).path
    return path if path else "/"


def _load_queue() -> pd.DataFrame:
    """Load priority + backlog from Google Sheets (or xlsx fallback), deduplicate, score, combine.

    Returns a single DataFrame with priority rows first (tagged "★ Priority")
    then backlog rows ("Backlog"), capped at 25 each.  Metric columns may be NaN.
    """
    if sheets_client.available():
        raw = sheets_client.read_queue()
        priority_df, backlog_df = workbook.parse_queue_raw(raw)
    else:
        priority_df, backlog_df = workbook.load_queue(DATA_PATH)

    # Score backlog first so we can copy metrics to priority rows that lack them.
    if not backlog_df.empty:
        backlog_df = workbook.opportunity_score(backlog_df)

    if not priority_df.empty and not backlog_df.empty:
        # Remove from backlog any URL that is already in the priority list.
        priority_urls = set(priority_df["url"])
        backlog_df = backlog_df[~backlog_df["url"].isin(priority_urls)].reset_index(drop=True)

        # Fill missing priority metrics from the backlog entry for the same URL.
        bl_idx = backlog_df.set_index("url")
        for col in ["clicks", "impressions", "ctr", "avg_position"]:
            def _fill(row, c=col):
                if pd.isna(row[c]) and row["url"] in bl_idx.index:
                    val = bl_idx.at[row["url"], c]
                    return val if not pd.isna(val) else row[c]
                return row[c]
            priority_df[col] = priority_df.apply(_fill, axis=1)

    # Score priority (after any metric fill from backlog).
    if not priority_df.empty:
        priority_df = workbook.opportunity_score(priority_df)
        priority_df["section"] = "★ Priority"

    if not backlog_df.empty:
        backlog_df["section"] = "Backlog"

    parts = [df for df in [
        priority_df.head(25) if not priority_df.empty else None,
        backlog_df.head(100) if not backlog_df.empty else None,
    ] if df is not None]

    if not parts:
        return pd.DataFrame(columns=["url", "clicks", "impressions", "ctr",
                                     "avg_position", "score", "why", "section"])

    combined = pd.concat(parts, ignore_index=True)
    combined["section"] = combined.get("section", "Backlog")
    return combined


def _get_queue() -> pd.DataFrame:
    global QUEUE_DF
    if QUEUE_DF is None:
        QUEUE_DF = _load_queue()
    return QUEUE_DF


def _queue_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["page"] = out["url"].apply(_short_path)
    has_metrics = df["clicks"].notna().any() or df["impressions"].notna().any()
    if has_metrics:
        def _fmt_int(x):
            return int(round(x)) if pd.notna(x) else "—"
        def _fmt_float1(x):
            return round(float(x), 1) if pd.notna(x) else "—"
        def _fmt_pct(x):
            return f"{x:.1%}" if pd.notna(x) else "—"
        out["impressions"] = out["impressions"].apply(_fmt_int)
        out["clicks"] = out["clicks"].apply(_fmt_int)
        out["ctr"] = out["ctr"].apply(_fmt_pct)
        out["avg_position"] = out["avg_position"].apply(_fmt_float1)
        out["score"] = out["score"].apply(_fmt_float1)
        cols = ["section", "page", "clicks", "impressions", "ctr", "avg_position", "score"]
    else:
        cols = ["section", "page"] if "section" in out.columns and out["section"].notna().any() else ["page"]
    return out[cols]



def _gsc_row_for_url(queue_df: pd.DataFrame, url: str) -> dict | None:
    normalized = workbook.normalize_url(url)
    matches = queue_df[queue_df["url"] == normalized]
    if matches.empty:
        return None
    row = matches.iloc[0]
    # Return None for any metric that is missing so callers don't treat 0 as real data.
    def _val(col):
        v = row[col]
        return float(v) if pd.notna(v) else None
    result = {c: _val(c) for c in ["clicks", "impressions", "ctr", "avg_position"]}
    # If all metrics are missing this page has no search data — still return the
    # dict so the URL is recognized as a known page.
    return result


# ---------------------------------------------------------------------------
# Runtime model config
# ---------------------------------------------------------------------------

RUNTIME = get_runtime_config()
GENERATOR_CHOICES = RUNTIME["all_generator_choices"]
GENERATOR_LABEL_TO_SPEC = {g["label"]: g for g in GENERATOR_CHOICES}
DEFAULT_GENERATOR_LABELS = [g["label"] for g in RUNTIME["generators"]]

# Judge choices = every model that could plausibly judge (generators ∪ judges),
# deduplicated by label. The UI enforces judge != selected generators at run time.
JUDGE_LABEL_TO_SPEC = {g["label"]: g for g in GENERATOR_CHOICES}
for j in RUNTIME["judges"]:
    JUDGE_LABEL_TO_SPEC.setdefault(j["label"], j)
JUDGE_CHOICES = list(JUDGE_LABEL_TO_SPEC.keys())
DEFAULT_JUDGE_LABEL = RUNTIME["judges"][0]["label"]

QUEUE_DF: "pd.DataFrame | None" = None
BRAND = _load_yaml("brand_constitution.yaml")
RUBRIC = _load_yaml("seo_rubric.yaml")
DATAFORSEO_CFG = _load_yaml("dataforseo.yaml")


# ---------------------------------------------------------------------------
# Section B: Analyze
# ---------------------------------------------------------------------------

def _run_stage(stage_name: str, fn, *args, **kwargs):
    """Run a pipeline stage, turning provider errors into a clean gr.Error
    instead of a raw stack trace in the UI."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        msg = str(exc)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "rate limit" in msg.lower() or "quota" in msg.lower():
            raise gr.Error(
                f"{stage_name} failed: the model provider's rate limit or quota was "
                f"exceeded. Wait a minute and try again, or pick a different model "
                f"in the Generator/Judge pickers above. (Details: {msg[:200]})"
            )
        raise gr.Error(f"{stage_name} failed: {msg[:300]}")


def analyze_page(
    url_box: str,
    generator_labels: list[str],
    judge_label: str,
    show_details: bool,
):
    """Generator: yields (all analyze_outputs..., analyze_status) tuples.

    Intermediate yields push a status string into analyze_status so the user
    sees live stage updates without relying on Gradio's progress popup.
    The last item in every yielded tuple is the analyze_status string.
    """
    # One gr.update() (no-op) for every output except the trailing analyze_status.
    # Count: 1(current_state) + 3(candidates) + 2(kw boxes) + 12(items) +
    #        12(body) + 2(kw_status+table) + 5(states) = 37
    _SKIP = tuple(gr.update() for _ in range(37))

    _HEADER = "⏳ Analyzing page… this can take up to 90s."
    _MAX_SECS = 90
    _start_time = time.time()

    def _prog(stage: str) -> tuple:
        elapsed = int(time.time() - _start_time)
        filled = min(20, int(elapsed / _MAX_SECS * 20))
        bar = "█" * filled + "░" * (20 - filled)
        return _SKIP + (f"{_HEADER}\n\n`{bar}` {elapsed}s\n\n{stage}",)

    if judge_label in generator_labels:
        raise gr.Error("The judge model must be different from the selected generator models.")
    if not generator_labels:
        raise gr.Error("Select at least one generator model.")

    raw_url = url_box.strip() if url_box and url_box.strip() else None
    if raw_url is None:
        raise gr.Error("Select a page from the queue above or enter a URL.")
    url = workbook.normalize_url(raw_url) or raw_url

    # Resolve model specs early so labels are available for status messages.
    generator_specs = [GENERATOR_LABEL_TO_SPEC[label] for label in generator_labels]
    judge_spec = JUDGE_LABEL_TO_SPEC[judge_label]
    gen_names = ", ".join(g["label"] for g in generator_specs)
    judge_name = judge_spec["label"]

    gsc_row = _gsc_row_for_url(_get_queue(), url)

    yield _prog("Fetching page…")
    snapshot = fetch_page(url)
    if snapshot.error:
        raise gr.Error(
            f"{snapshot.error} — check the URL is correct and the page is published."
        )

    yield _prog(f"Generating with {gen_names}…")
    candidates = _run_stage("Generating candidates", generate_candidates, snapshot, BRAND, RUBRIC, gsc_row, None, generator_specs)

    yield _prog("Enriching keywords…")
    seed_keywords = pool_seed_keywords(candidates)
    enriched_pool, keyword_status = enrich(seed_keywords, DATAFORSEO_CFG)

    yield _prog(f"Synthesizing with {judge_name}…")
    plan = _run_stage(
        "Judge synthesis",
        synthesize,
        candidates,
        snapshot,
        BRAND,
        RUBRIC,
        gsc_row,
        judge_spec["model_config_id"],
        enriched_pool,
        DATAFORSEO_CFG,
    )

    yield _prog(f"Checking brand fit with {judge_name}…")
    brand_flags = _run_stage("Brand-fit verification", verify_brand_fit, plan, BRAND, judge_spec["model_config_id"])
    flag_by_field = {f.field: f for f in brand_flags if f.status != "ok"}

    # --- Current state panel ---
    legacy_notice = ""
    if snapshot.legacy_sections_found:
        names = ", ".join(h.split(" (near:")[0] for h in snapshot.legacy_sections_found)
        legacy_notice = (
            f"\n\n**Found {len(snapshot.legacy_sections_found)} legacy section(s) to remove on this page: "
            f"{names} — removal is included in the plan automatically.**"
        )

    current_state_md = (
        f"### Current state — {snapshot.url}\n"
        f"- **Title:** {snapshot.title}\n"
        f"- **Meta description:** {snapshot.meta_description}\n"
        f"- **H1:** {snapshot.h1}\n"
        f"- **Headings:** {', '.join(snapshot.headings) if snapshot.headings else '(none found)'}"
        f"{legacy_notice}"
    )

    # --- Candidates ---
    candidate_updates = []
    for i in range(MAX_GENERATORS):
        if i < len(candidates):
            c = candidates[i]
            md = (
                f"#### {c.model_label}\n"
                f"- **H1 options:** {c.h1_options}\n"
                f"- **URL slug:** {c.url_slug}\n"
                f"- **SEO title:** {c.seo_title}\n"
                f"- **Meta description:** {c.meta_description}\n"
                f"- **Body change:** {c.body_change}\n"
                f"- **Keywords:** {c.keywords}\n"
                f"- **Keep current:** {c.keep_current}"
            )
            candidate_updates.append(gr.update(value=md))
        else:
            candidate_updates.append(gr.update(value=""))

    # --- Plan items ---
    item_updates = []
    for i in range(MAX_PLAN_ITEMS):
        if i < len(plan.items):
            item = plan.items[i]
            flag = flag_by_field.get(item.field)
            flag_md = f"\n\n⚠ **Voice check:** {flag.note}" if flag else ""
            info_md = (
                f"**{item.field}**\n"
                f"- Source: {item.source}\n"
                f"- Rationale: {item.rationale}\n"
                f"- Rubric check: {item.rubric_check}\n"
                f"- Brand check: {item.brand_check}{flag_md}"
            )
            counter = ""
            if item.field == "seo_title":
                counter = f"{len(item.decision)}/{RUBRIC['seo_title']['max_chars']} chars"
            elif item.field == "meta_description":
                counter = f"{len(item.decision)}/{RUBRIC['meta_description']['max_chars']} chars"
            item_updates.append(gr.update(label=f"{item.field} — decision", value=item.decision))
            item_updates.append(gr.update(value=info_md if show_details else ""))
            item_updates.append(gr.update(value=counter))
        else:
            item_updates.append(gr.update(label="", value=""))
            item_updates.append(gr.update(value=""))
            item_updates.append(gr.update(value=""))

    # Note how many changes exceed the review UI (don't truncate — auto changes must survive)
    n_body_extra = max(0, len(plan.body_changes) - MAX_BODY_CHANGES)

    # --- Body changes ---
    body_updates = []
    for i in range(MAX_BODY_CHANGES):
        if i < len(plan.body_changes):
            bc = plan.body_changes[i]
            tag = " (automatic — from your procedure)" if bc.automatic else ""
            label = f"{i + 1}. [{bc.action}]{tag}  Anchor: \"{bc.anchor_text}\""
            body_updates.append(gr.update(label=label, value=bc.instruction))
            body_updates.append(gr.update(value=bc.new_text or "", label="New text"))
        else:
            body_updates.append(gr.update(label="", value=""))
            body_updates.append(gr.update(label="", value=""))

    primary_kw = plan.primary_keyword
    secondary_kw = ", ".join(plan.secondary_keywords)

    # --- Keyword enrichment status + pool table ---
    dfs_mode = DATAFORSEO_CFG.get("mode", "sandbox")
    if keyword_status["connected"] and not keyword_status["error"]:
        keyword_status_md = (
            f"🔗 **Keyword data:** connected to DataForSEO ({dfs_mode} mode) "
            f"— {len(plan.keyword_pool)} keyword(s) evaluated below."
        )
    elif keyword_status["connected"] and keyword_status["error"]:
        keyword_status_md = (
            f"🔗 **Keyword data:** connected to DataForSEO ({dfs_mode} mode), partial data "
            f"— {len(plan.keyword_pool)} keyword(s) evaluated. Note: {keyword_status['error'][:200]}"
        )
    elif keyword_status["error"]:
        keyword_status_md = (
            f"⚠️ **Keyword data:** enrichment failed ({keyword_status['error'][:150]}) — "
            f"the run completed with ungrounded keywords."
        )
    else:
        keyword_status_md = (
            "ℹ️ **Keyword data:** not connected. Set `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` "
            "in `.env` for search-volume- and difficulty-grounded keyword suggestions."
        )

    keyword_pool_df = _keyword_pool_display(plan)

    # Warn if body changes exceed the review UI (extras still reach the paste-pack unchanged)
    if n_body_extra:
        keyword_status_md += (
            f"\n\n⚠️ **{n_body_extra} body change(s) beyond the review UI** — the "
            f"{MAX_BODY_CHANGES}-slot review shows the first {MAX_BODY_CHANGES} only. "
            f"Extra changes are included in the paste-pack as-is."
        )

    # Validate that the judge's keyword picks exist in the enriched pool
    pool_terms_lower = {entry["term"].lower() for entry in enriched_pool}
    invented_kws = []
    if plan.primary_keyword and plan.primary_keyword.lower() not in pool_terms_lower:
        invented_kws.append(f"primary: '{plan.primary_keyword}'")
    for k in plan.secondary_keywords:
        if k and k.lower() not in pool_terms_lower:
            invented_kws.append(f"secondary: '{k}'")
    if invented_kws:
        keyword_status_md += (
            f"\n\n⚠️ **Invented keyword(s):** judge selected term(s) not in the enriched pool — "
            f"{', '.join(invented_kws)}. Edit the keyword fields above to correct."
        )

    yield (
        current_state_md,
        *candidate_updates,
        primary_kw, secondary_kw,
        *item_updates,
        *body_updates,
        keyword_status_md, keyword_pool_df,
        snapshot, candidates, plan, gsc_row, brand_flags,
        "✅ Analysis complete — review the plan below.",
    )


def _char_counter(value: str, max_chars: int) -> str:
    n = len(value)
    mark = "✓" if n <= max_chars else "✗"
    return f"{n}/{max_chars} chars {mark}"


def _keyword_pool_display(plan: ExecutionPlan) -> pd.DataFrame:
    """Evaluated keyword pool for the review table: selected + on-voice terms
    first, flagged (off-voice) terms last.
    """
    columns = ["keyword", "volume", "difficulty", "voice", "viability", "note", "selected"]
    if not plan.keyword_pool:
        return pd.DataFrame(columns=columns)

    rows = []
    for kw in plan.keyword_pool:
        flagged = "flagged" in kw.category.lower()
        rows.append(
            {
                "keyword": kw.term,
                "volume": kw.volume if kw.has_data and kw.volume is not None else "no data",
                "difficulty": kw.difficulty if kw.has_data and kw.difficulty is not None else "no data",
                "voice": kw.voice_score,
                "viability": kw.viability_score,
                "note": kw.note,
                "selected": "✓" if kw.selected else "",
                "_sort": (0 if kw.selected else 1, 1 if flagged else 0, -kw.viability_score),
            }
        )
    df = pd.DataFrame(rows).sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Section C: Approve
# ---------------------------------------------------------------------------

def approve(
    snapshot: PageSnapshot,
    plan: ExecutionPlan,
    gsc_row: dict | None,
    primary_kw: str,
    secondary_kw: str,
    *item_and_body_values,
):
    if plan is None or snapshot is None:
        raise gr.Error("Run 'Analyze page' first.")

    item_values = item_and_body_values[:MAX_PLAN_ITEMS]
    body_values = item_and_body_values[MAX_PLAN_ITEMS:]

    # Apply edits from the review UI back onto the plan.
    for i, item in enumerate(plan.items):
        if i < len(item_values) and item_values[i] is not None:
            item.decision = item_values[i]
    plan.primary_keyword = primary_kw
    plan.secondary_keywords = [k.strip() for k in secondary_kw.split(",") if k.strip()]

    for i, bc in enumerate(plan.body_changes):
        instr_idx, text_idx = 2 * i, 2 * i + 1
        if instr_idx < len(body_values) and body_values[instr_idx] is not None:
            bc.instruction = body_values[instr_idx]
        if text_idx < len(body_values) and body_values[text_idx] is not None:
            bc.new_text = body_values[text_idx] or None

    ad_assets = generate_ad_assets(snapshot, plan)
    result = render_pastepack(plan, snapshot, operator="admin", ad_assets=ad_assets)

    drive_note = ""
    if sheets_client.available():
        try:
            docx_filename = Path(result["docx_path"]).name
            drive_url = sheets_client.upload_docx(result["docx_path"], docx_filename)
            drive_note = f"\n\n📁 [Saved to Google Drive]({drive_url})"
        except Exception as exc:
            drive_note = f"\n\n⚠️ Drive upload failed: {exc}"

    confirmation = (
        "**Next:** make the Squarespace edits, then manually move the URL to "
        f"'URLs Done' in the Google Sheet.{drive_note}"
    )

    return result["markdown"], confirmation, gr.update(visible=True, value=result["docx_path"])


# ---------------------------------------------------------------------------
# Build the Gradio app
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    with gr.Blocks(title="SEO Workflow") as demo:
        gr.Markdown("# SEO Workflow")

        # --- Batch Run ---
        gr.Markdown("## Batch Run")
        gr.Markdown(
            "Trigger a Cloud Run batch job to process all pending URLs in the queue. "
            "The job runs in the background — docx files and a Google Ads Editor CSV "
            "are uploaded to Shared Drive automatically when done."
        )
        with gr.Row():
            batch_limit_box = gr.Textbox(
                label="Max URLs to process (leave blank = all pending)",
                placeholder="e.g. 10",
                value="",
                scale=1,
            )
            batch_btn = gr.Button("▶ Start Batch Job", variant="primary", scale=1)
        batch_status = gr.Markdown()

        def _run_batch(limit_raw: str):
            limit_raw = (limit_raw or "").strip()
            limit = int(limit_raw) if limit_raw.isdigit() else None
            try:
                from .cloud_run_job import run_batch_with_status
            except Exception as exc:
                yield f"❌ Cloud Run module unavailable: {exc}"
                return
            yield from run_batch_with_status(limit)

        batch_btn.click(
            _run_batch,
            inputs=[batch_limit_box],
            outputs=[batch_status],
        )

        gr.Markdown("---")

        if RUNTIME.get("warning"):
            gr.Markdown(f"⚠️ {RUNTIME['warning']}")

        # --- Section A: the queue ---
        gr.Markdown("## A. The queue")
        queue_table = gr.Dataframe(value=None, interactive=False, wrap=True)

        url_box = gr.Textbox(
            label="Select a URL above or enter URL directly",
            placeholder="https://herondance.org/...",
        )

        refresh_btn = gr.Button("↻ Refresh queue", size="sm")

        def _fill_url_from_table(evt: gr.SelectData) -> str:
            row_idx = evt.index[0]
            return _get_queue().iloc[row_idx]["url"]

        def _refresh_queue():
            global QUEUE_DF
            QUEUE_DF = _load_queue()
            return _queue_display(QUEUE_DF)

        queue_table.select(_fill_url_from_table, outputs=[url_box])
        refresh_btn.click(_refresh_queue, outputs=[queue_table])

        with gr.Row():
            generator_picker = gr.CheckboxGroup(
                choices=[g["label"] for g in GENERATOR_CHOICES],
                value=DEFAULT_GENERATOR_LABELS,
                label="Generator models",
            )
            judge_picker = gr.Dropdown(
                choices=JUDGE_CHOICES,
                value=DEFAULT_JUDGE_LABEL,
                label="Judge model (must differ from generators)",
            )

        analyze_btn = gr.Button("Analyze page", variant="primary")
        analyze_status = gr.Markdown()

        with gr.Group(visible=False) as review_group:
            # --- Section B: run + review ---
            gr.Markdown("## B. Run + review")
            current_state = gr.Markdown()

            gr.Markdown("### Candidates")
            with gr.Row():
                candidate_panels = [gr.Markdown(value="") for _ in range(MAX_GENERATORS)]

            gr.Markdown("### Judge's execution plan")
            show_details = gr.Checkbox(
                label="Show details (rationale, rubric check, brand check, voice check)",
                value=False,
            )
            with gr.Row():
                primary_kw_box = gr.Textbox(label="Primary keyword")
                secondary_kw_box = gr.Textbox(label="Secondary keywords (comma-separated)")

            gr.Markdown("### Evaluated keyword pool")
            keyword_status_md = gr.Markdown()
            keyword_pool_table = gr.Dataframe(
                headers=["keyword", "volume", "difficulty", "voice", "viability", "note", "selected"],
                interactive=False,
                wrap=True,
            )
            with gr.Row():
                gr.Markdown(
                    "**Voice (1–5):** judge's read on brand/voice fit — 5 = perfectly contemplative/literary, "
                    "1 = generic or off-brand. &nbsp;|&nbsp; "
                    "**Viability (1–5):** derived from DataForSEO data — 5 = low difficulty + good volume "
                    "(winnable for a low-authority site), 1 = highly competitive. &nbsp;|&nbsp; "
                    "Rows sorted: judge's picks first, then by viability. &nbsp;|&nbsp; "
                    "_To override keyword choices, edit Primary / Secondary fields above. "
                    "Volume and difficulty are estimates — least reliable at very low volume._"
                )

            item_decisions = []
            item_infos = []
            item_counters = []
            for i in range(MAX_PLAN_ITEMS):
                with gr.Row():
                    d = gr.Textbox(label="", value="")
                    info = gr.Markdown(value="")
                    counter = gr.Markdown(value="")
                item_decisions.append(d)
                item_infos.append(info)
                item_counters.append(counter)

            gr.Markdown("### Body content changes")
            body_instructions = []
            body_texts = []
            body_rows = []
            for i in range(MAX_BODY_CHANGES):
                with gr.Row() as body_row:
                    instr = gr.Textbox(lines=2, label="", value="")
                    text = gr.Textbox(lines=3, label="", value="")
                body_instructions.append(instr)
                body_texts.append(text)
                body_rows.append(body_row)

            # --- Section C: approve ---
            gr.Markdown("## C. Approve")
            approve_btn = gr.Button("Approve and generate Squarespace paste-pack", variant="primary")
            approve_status = gr.Markdown()
            pastepack_md = gr.Markdown()
            confirmation_md = gr.Markdown()
            docx_download = gr.DownloadButton(label="Download Squarespace paste-pack (.docx)", visible=False, variant="primary")

        # State
        snapshot_state = gr.State()
        candidates_state = gr.State()
        plan_state = gr.State()
        gsc_row_state = gr.State()
        brand_flags_state = gr.State()

        # review_group is NOT in analyze_outputs — its visibility is toggled in a
        # separate .then() after analyze_page finishes, so Gradio applies the value
        # updates first and the show/hide second (avoids a Gradio 6 rendering bug
        # where values set in the same batch as visible=True are ignored).
        # analyze_status is last: analyze_page (a generator) yields stage messages
        # into it so the user sees live progress without relying on Gradio's popup.
        analyze_outputs = (
            [current_state]
            + candidate_panels
            + [primary_kw_box, secondary_kw_box]
            + [x for triple in zip(item_decisions, item_infos, item_counters) for x in triple]
            + [x for pair in zip(body_instructions, body_texts) for x in pair]
            + [keyword_status_md, keyword_pool_table]
            + [snapshot_state, candidates_state, plan_state, gsc_row_state, brand_flags_state]
            + [analyze_status]
        )

        def _toggle_details(show: bool, plan: ExecutionPlan | None, brand_flags: list | None):
            if not plan:
                return [gr.update(value="") for _ in range(MAX_PLAN_ITEMS)]
            flag_by_field = {f.field: f for f in (brand_flags or []) if f.status != "ok"}
            result = []
            for i in range(MAX_PLAN_ITEMS):
                if i < len(plan.items):
                    item = plan.items[i]
                    flag = flag_by_field.get(item.field)
                    flag_md = f"\n\n⚠ **Voice check:** {flag.note}" if flag else ""
                    info_md = (
                        f"**{item.field}**\n"
                        f"- Source: {item.source}\n"
                        f"- Rationale: {item.rationale}\n"
                        f"- Rubric check: {item.rubric_check}\n"
                        f"- Brand check: {item.brand_check}{flag_md}"
                    )
                    result.append(gr.update(value=info_md if show else ""))
                else:
                    result.append(gr.update(value=""))
            return result

        show_details.change(
            _toggle_details,
            inputs=[show_details, plan_state, brand_flags_state],
            outputs=item_infos,
        )

        def _show_body_rows(plan: ExecutionPlan | None):
            """Show only the body-change rows that have actual content."""
            if not plan:
                return [gr.update(visible=False)] * MAX_BODY_CHANGES
            n = len(plan.body_changes)
            return [gr.update(visible=(i < n)) for i in range(MAX_BODY_CHANGES)]

        # Live char counters re-validate on edit.
        for i, (decision_box, counter_box) in enumerate(zip(item_decisions, item_counters)):
            def _make_recount(field_idx):
                def _recount(value, plan: ExecutionPlan | None):
                    if plan is None or field_idx >= len(plan.items):
                        return gr.update()
                    field = plan.items[field_idx].field
                    if field == "seo_title":
                        return _char_counter(value, RUBRIC["seo_title"]["max_chars"])
                    if field == "meta_description":
                        return _char_counter(value, RUBRIC["meta_description"]["max_chars"])
                    return gr.update()

                return _recount

            decision_box.change(_make_recount(i), inputs=[decision_box, plan_state], outputs=[counter_box])

        approve_btn.click(
            lambda: "⏳ Generating paste-pack…",
            outputs=[approve_status],
        ).then(
            approve,
            inputs=[snapshot_state, plan_state, gsc_row_state, primary_kw_box, secondary_kw_box]
            + item_decisions
            + [x for pair in zip(body_instructions, body_texts) for x in pair],
            outputs=[pastepack_md, confirmation_md, docx_download],
        ).then(
            lambda: "",
            outputs=[approve_status],
        )

        # Wire Analyze button — clear B+C immediately, then run analysis.
        # review_group sits at the front of clear_outputs but NOT in analyze_outputs
        # (its visibility is managed in a separate .then() step; see note above).
        clear_outputs = (
            [review_group]
            + analyze_outputs
            + [approve_status, pastepack_md, confirmation_md, docx_download]
        )

        def _start_analysis():
            blank_candidates = [gr.update(value="")] * MAX_GENERATORS
            blank_items = [gr.update(label="", value="")] * (MAX_PLAN_ITEMS * 3)
            blank_body = [gr.update(label="", value="")] * (MAX_BODY_CHANGES * 2)
            return (
                [gr.update(visible=False)]     # review_group (front of clear_outputs)
                + [""]                         # current_state
                + blank_candidates
                + ["", ""]                     # primary_kw_box, secondary_kw_box
                + blank_items
                + blank_body
                + ["", pd.DataFrame()]         # keyword_status_md, keyword_pool_table
                + [None, None, None, None, []] # states + brand_flags
                + ["⏳ Analyzing page… this can take up to 90s."]  # analyze_status
                + ["", "", "", gr.update(visible=False, value=None)]  # approve section
            )

        analyze_btn.click(
            _start_analysis,
            outputs=clear_outputs,
            show_progress="hidden",
        ).then(
            analyze_page,
            inputs=[url_box, generator_picker, judge_picker, show_details],
            outputs=analyze_outputs,
            show_progress="hidden",
        ).then(
            # Toggle body-row visibility AFTER values are set — doing this in the same
            # batch as analyze_page triggers the Gradio 6 visibility+value batch bug.
            _show_body_rows,
            inputs=[plan_state],
            outputs=body_rows,
            show_progress="hidden",
        ).then(
            # Reveal the results group last — same reason as above.
            lambda: gr.update(visible=True),
            outputs=[review_group],
            show_progress="hidden",
        )

        demo.load(_refresh_queue, outputs=[queue_table])

    return demo


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    _password = os.environ.get("APP_PASSWORD")
    if not _password:
        raise RuntimeError(
            "APP_PASSWORD is not set. Add it to .env for local dev "
            "or ensure the Cloud Run secret is wired in."
        )

    app = build_app()
    app.queue(max_size=10)
    app.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 8080)),
        auth=(os.environ.get("APP_USERNAME", "admin"), _password),
        max_threads=10,
    )
