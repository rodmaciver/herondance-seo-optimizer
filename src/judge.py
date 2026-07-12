"""Stage 2: judge synthesis, mechanical validation, and brand-fit verification."""
from __future__ import annotations

import re
from pathlib import Path

import yaml
import json as _json

from pydantic import BaseModel, field_validator

from .keyword_enrichment import category_label, score_viability
from .model_clients import call_model
from .schema import BodyChange, BrandFlag, CandidateSet, EvaluatedKeyword, ExecutionPlan, PageSnapshot

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

JUDGE_SYSTEM_PROMPT = """\
You are the final decision-maker in an SEO workflow for a contemplative
literary website. You will be given the site's brand constitution, an SEO
rubric, a page snapshot with GSC stats, and recommendations from {n}
candidate models (each labeled).

For each field (seo_title, h1, url_slug, meta_description, keywords), make
an executive decision: pick one candidate's suggestion, blend the best
parts of several, or keep the current value. "keep current" is a first-class
answer. For each decision give a ONE-sentence rationale, a rubric_check
(mechanical compliance, e.g. "58 chars ✓ | primary keyword at front ✓"),
and a brand_check (ONE sentence on how this honors or trades against the
brand's guiding principle).

CRITICAL — field definitions for each plan item:
- `decision`: the ACTUAL resolved text (e.g. the full SEO title string you
  have chosen). The only special literal is "keep current" when you
  recommend no change. NEVER write process words like "blend",
  "Claude's suggestion", "GPT's suggestion" here — those belong in `source`.
- `source`: label describing where the value came from — e.g. "Claude",
  "GPT", "blended", or "keep current". This is a label, not the value.

For body_changes, synthesize MULTIPLE concrete content changes (the
candidates each proposed only one) -- the exact headings/text changes and
exactly where they go. Every body_change MUST include an `anchor_text` that
is an EXACT VERBATIM quote copied from the page body text provided, so a
human can find it in the page editor without guessing.
The `instruction` field describes LOCATION and ACTION only — never embed
the new_text content inside the instruction. The content belongs in `new_text`.
  BAD:  "Insert a new heading: Zhuangzi's Butterfly Dream"
  GOOD: "Immediately before the sentence 'A man dreams he is a butterfly.', insert a new heading."
The instruction must also be self-contained about location — never write
"after this sentence", "before this paragraph", or any phrasing that uses a
pronoun or positional reference without quoting the actual text:
  BAD:  "After this sentence, add a paragraph about…"
  GOOD: "After the sentence 'Do you not see that you and I…', add a paragraph about…"
`new_text` must be plain text only — no Markdown syntax (no ##, **, *, etc.).
For headings, write just the heading words; for body text, write just the prose.
Do NOT include notes like "(not using Markdown syntax)" anywhere in instruction or new_text.

If the page's URL slug changes, set redirect_mapping to
"<old-path> -> <new-path> 301"; otherwise set it to null.

KEYWORDS -- you will be given a grounded keyword pool (every keyword
candidate proposed by every model, plus related terms, each annotated with
real search volume and difficulty from DataForSEO where available, or
"no data" if not). You MUST choose primary_keyword and the secondary_keywords
ONLY from this pool -- do not invent new keywords. Volume/difficulty are
estimates and least reliable at very low volume; use them as a guide, not a
hard rule, and weigh them alongside semantic/brand fit.

You must ALSO return a `keyword_pool` array with exactly ONE entry per
keyword pool item given to you. For each entry:
- term: copy the keyword exactly as given
- volume, difficulty, has_data: copy these through unchanged from the input
- voice_score (1-5): how well this term matches the brand's contemplative/
  literary voice (5 = perfectly on-voice, 1 = generic self-help/wellness/
  off-voice)
- note: ONE short phrase explaining the voice_score
- viability_score: leave as 3 (it will be recomputed from real data)
- category: leave as "" (it will be recomputed)
- selected: leave as false (it will be derived from your primary/secondary picks)
"""


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def _format_candidates(candidates: list[CandidateSet]) -> str:
    parts = []
    for c in candidates:
        parts.append(
            f"--- Candidate: {c.model_label} ---\n"
            f"h1_options: {c.h1_options}\n"
            f"url_slug: {c.url_slug}\n"
            f"seo_title: {c.seo_title}\n"
            f"meta_description: {c.meta_description}\n"
            f"body_change: {c.body_change}\n"
            f"keywords: {c.keywords}\n"
            f"keep_current: {c.keep_current}\n"
        )
    return "\n".join(parts)


def _format_keyword_pool(pool: list[dict]) -> str:
    lines = ["Grounded keyword pool (term | volume | difficulty):"]
    for entry in pool:
        vol = entry["volume"] if entry.get("has_data") and entry.get("volume") is not None else "no data"
        diff = entry["difficulty"] if entry.get("has_data") and entry.get("difficulty") is not None else "no data"
        lines.append(f"- {entry['term']} | volume: {vol} | difficulty: {diff}")
    return "\n".join(lines)


def build_judge_prompt(
    candidates: list[CandidateSet],
    snapshot: PageSnapshot,
    brand: dict,
    rubric: dict,
    gsc_row: dict | None,
    keyword_pool: list[dict],
) -> tuple[str, str]:
    system = (
        JUDGE_SYSTEM_PROMPT.format(n=len(candidates))
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
    ]
    if gsc_row and any(v is not None for v in gsc_row.values()):
        clicks = gsc_row.get("clicks")
        impressions = gsc_row.get("impressions")
        position = gsc_row.get("avg_position")
        ctr = gsc_row.get("ctr")
        parts = []
        if clicks is not None:
            parts.append(f"{clicks:.0f} clicks")
        if impressions is not None:
            parts.append(f"{impressions:.0f} impressions")
        if position is not None:
            parts.append(f"position {position:.1f}")
        if ctr is not None:
            parts.append(f"CTR {ctr:.2%}")
        user_parts.append("GSC stats: " + ", ".join(parts) if parts else "No GSC stats available.")
    user_parts.append("Candidate recommendations:\n" + _format_candidates(candidates))
    user_parts.append(_format_keyword_pool(keyword_pool))
    return system, "\n\n".join(user_parts)


# ---------------------------------------------------------------------------
# Mechanical post-validation (does not trust the model)
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _annotate(rubric_check: str, ok: bool, note: str) -> str:
    mark = "✓" if ok else "✗"
    suffix = f"{note} {mark}"
    return f"{rubric_check} | {suffix}" if rubric_check else suffix


def _banned_voice_hits(text: str, banned: list[str]) -> list[str]:
    """Return banned marketing phrases found in text (word-boundary match)."""
    hits = []
    for phrase in banned:
        if re.search(r"\b" + re.escape(str(phrase)) + r"\b", text, re.IGNORECASE):
            hits.append(str(phrase))
    return hits


def validate_plan(plan: ExecutionPlan, rubric: dict, brand: dict | None = None) -> ExecutionPlan:
    """Mechanically check rubric compliance and annotate rubric_check fields."""
    banned = (brand or {}).get("voice", {}).get("banned_marketing_language", [])
    _READER_FACING = {"seo_title", "h1", "meta_description"}
    for item in plan.items:
        if (
            banned
            and item.field in _READER_FACING
            and item.decision.lower() != "keep current"
        ):
            hits = _banned_voice_hits(item.decision, banned)
            if hits:
                item.brand_check = _annotate(
                    item.brand_check,
                    False,
                    "banned marketing language: " + ", ".join(hits),
                )
    for item in plan.items:
        if item.field == "seo_title" and item.decision.lower() != "keep current":
            max_chars = rubric["seo_title"]["max_chars"]
            n = len(item.decision)
            item.rubric_check = _annotate(item.rubric_check, n <= max_chars, f"{n} chars (max {max_chars})")
        elif item.field == "meta_description" and item.decision.lower() != "keep current":
            max_chars = rubric["meta_description"]["max_chars"]
            n = len(item.decision)
            item.rubric_check = _annotate(item.rubric_check, n <= max_chars, f"{n} chars (max {max_chars})")
        elif item.field == "url_slug" and item.decision.lower() != "keep current":
            slug = item.decision.strip("/").lower()
            word_count = len(slug.split("-"))
            ok = bool(_SLUG_RE.match(slug)) and 1 <= word_count <= 6
            item.rubric_check = _annotate(item.rubric_check, ok, f"slug format '{slug}'")
    return plan


# ---------------------------------------------------------------------------
# Deterministic standard-section enforcement (NOT judge discretion)
# ---------------------------------------------------------------------------

def enforce_standard_sections(plan: ExecutionPlan, snapshot: PageSnapshot) -> ExecutionPlan:
    """Guarantee a remove_section change per detected legacy section, and one
    add_section change for the Tao Te Ching Journal, regardless of what the
    judge produced.
    """
    standard_sections = _load_yaml("standard_sections.yaml")

    # Dedup on instruction text — anchor_text from the judge is a body excerpt
    # and will never match the section name, so it can't be used for dedup.
    existing_remove_instructions = {
        bc.instruction for bc in plan.body_changes if bc.action == "remove_section"
    }
    for hit in snapshot.legacy_sections_found:
        # hit looks like "Section Name (near: 'H2: heading text')"
        section_name = hit.split(" (near:")[0]
        instruction = f"Remove the legacy section '{section_name}' from this page."
        if instruction not in existing_remove_instructions:
            match = re.search(r"near: '(?:H\d: )?(.*)'\)$", hit)
            anchor = match.group(1) if match else section_name
            plan.body_changes.append(
                BodyChange(
                    action="remove_section",
                    anchor_text=anchor,
                    instruction=instruction,
                    new_text=None,
                    automatic=True,
                )
            )

    has_add_section = any(bc.action == "add_section" for bc in plan.body_changes)
    if not has_add_section and snapshot.body_text:
        section_cfg = standard_sections["current_section_to_add"]
        plan.body_changes.append(
            BodyChange(
                action="add_section",
                anchor_text="end of page",
                instruction=f"Scroll to the bottom of the page content. Add the standard '{section_cfg['title']}' section there.",
                new_text=None,
                automatic=True,
            )
        )

    return plan


# ---------------------------------------------------------------------------
# Stage 2 entry point
# ---------------------------------------------------------------------------

def _finalize_keyword_pool(plan: ExecutionPlan, enriched_pool: list[dict], dataforseo_cfg: dict) -> ExecutionPlan:
    """Overwrite volume/difficulty/has_data with our authoritative DataForSEO
    data, derive viability_score/category deterministically from config
    thresholds, and derive `selected` from the judge's primary/secondary
    picks. Never drops a keyword -- low-voice terms are flagged via
    `category`, not removed.
    """
    thresholds = dataforseo_cfg.get("thresholds", {})
    categories = dataforseo_cfg.get("categories", {})
    voice_by_term = {kw.term.lower(): kw for kw in plan.keyword_pool}
    selected_terms = {plan.primary_keyword.lower()} | {k.lower() for k in plan.secondary_keywords}

    final_pool: list[EvaluatedKeyword] = []
    for entry in enriched_pool:
        term = entry["term"]
        voice_entry = voice_by_term.get(term.lower())
        voice_score = voice_entry.voice_score if voice_entry else 3
        note = voice_entry.note if voice_entry else ""
        viability_score, tier = score_viability(entry, thresholds)
        on_voice = voice_score >= thresholds.get("voice_flag_threshold", 3)
        category = category_label(tier, on_voice, categories)
        final_pool.append(
            EvaluatedKeyword(
                term=term,
                volume=entry.get("volume"),
                difficulty=entry.get("difficulty"),
                has_data=entry.get("has_data", False),
                voice_score=voice_score,
                viability_score=viability_score,
                note=note,
                category=category,
                selected=term.lower() in selected_terms,
            )
        )
    plan.keyword_pool = final_pool
    return plan


def _validation_failures(plan: ExecutionPlan) -> list[str]:
    """Return a list of rubric/brand failure strings from an already-validated plan."""
    failures = []
    for item in plan.items:
        if "✗" in (item.rubric_check or ""):
            failures.append(f"{item.field} rubric: {item.rubric_check}")
        if "✗" in (item.brand_check or ""):
            failures.append(f"{item.field} brand: {item.brand_check}")
    return failures


def synthesize(
    candidates: list[CandidateSet],
    snapshot: PageSnapshot,
    brand: dict,
    rubric: dict,
    gsc_row: dict | None,
    judge_model_id: str,
    enriched_pool: list[dict],
    dataforseo_cfg: dict,
) -> ExecutionPlan:
    system, user = build_judge_prompt(candidates, snapshot, brand, rubric, gsc_row, enriched_pool)

    def _run(user_prompt: str) -> ExecutionPlan:
        result = call_model(judge_model_id, system, user_prompt, ExecutionPlan, temperature=0.3)
        result["page_url"] = snapshot.url
        # Belt-and-suspenders: Claude occasionally returns list/dict fields as JSON
        # strings inside tool_use blocks. Unwrap any that slipped through.
        for _k, _v in list(result.items()):
            if isinstance(_v, str) and _v.strip()[:1] in ("[", "{"):
                try:
                    result[_k] = _json.loads(_v)
                except (ValueError, _json.JSONDecodeError):
                    pass
        plan = ExecutionPlan(**result)
        plan = validate_plan(plan, rubric, brand)
        return plan

    plan = _run(user)
    failures = _validation_failures(plan)
    if failures:
        retry_prompt = (
            user
            + "\n\nPREVIOUS ATTEMPT FAILED VALIDATION — fix only these issues and regenerate:\n"
            + "\n".join(f"- {f}" for f in failures)
        )
        plan = _run(retry_prompt)

    plan = enforce_standard_sections(plan, snapshot)
    plan = _finalize_keyword_pool(plan, enriched_pool, dataforseo_cfg)
    return plan


# ---------------------------------------------------------------------------
# Brand-fit verification (separate call, critiques output it didn't write)
# ---------------------------------------------------------------------------

class _BrandFitResponse(BaseModel):
    flags: list[BrandFlag]

    @field_validator("flags", mode="before")
    @classmethod
    def _coerce_flags(cls, v):
        if isinstance(v, str):
            v = _json.loads(v)
        return v


BRAND_FIT_SYSTEM_PROMPT = """\
You are the guardian of this contemplative website's voice. You did NOT
write the following decisions -- your job is to critique them.

You will be given the site's brand constitution (guiding principle and
constraints, verbatim) and a set of FINAL decided values for an SEO update.

For each field, return either:
- status "ok", or
- status "flag" with a one-sentence note QUOTING the offending phrase,
  if the decision drifts toward generic self-help, broad wellness, or
  diluted lifestyle language, or chases traffic at the cost of the site's
  contemplative identity.

Be specific and quote the actual words you're flagging.
"""


def verify_brand_fit(plan: ExecutionPlan, brand: dict, judge_model_id: str) -> list[BrandFlag]:
    system = BRAND_FIT_SYSTEM_PROMPT + "\n\nBrand constitution:\n" + yaml.dump(brand)
    lines = [f"Primary keyword: {plan.primary_keyword}", f"Secondary keywords: {plan.secondary_keywords}"]
    for item in plan.items:
        lines.append(f"{item.field}: {item.decision}")
    for bc in plan.body_changes:
        lines.append(f"body_change ({bc.action}): {bc.instruction} -> {bc.new_text or ''}")
    user = "Final decided values:\n" + "\n".join(lines)

    result = call_model(judge_model_id, system, user, _BrandFitResponse, temperature=0.2)
    return _BrandFitResponse(**result).flags
