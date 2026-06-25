"""Pydantic data contracts shared across the pipeline stages."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PageSnapshot(BaseModel):
    url: str
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    headings: list[str] = Field(default_factory=list)  # e.g. ["H2: ...", "H4: ..."]
    body_text: str = ""
    truncated: bool = False
    legacy_sections_found: list[str] = Field(default_factory=list)
    error: str | None = None


class CandidateSet(BaseModel):
    model_label: str
    h1_options: list[str]
    url_slug: str  # or "keep current"
    seo_title: str
    meta_description: str
    body_change: str
    keywords: list[str]
    keep_current: list[str] = Field(default_factory=list)


class BodyChange(BaseModel):
    action: str  # "add_heading" | "change_heading" | "revise_text" | "remove_section" | "add_section"
    anchor_text: str
    instruction: str
    new_text: str | None = None
    automatic: bool = False  # marks deterministic standard-section enforcement


class PlanItem(BaseModel):
    field: str  # "seo_title" | "h1" | "url_slug" | "meta_description" | "keywords"
    decision: str
    source: str  # which candidate it came from, "blended", or "judge's own"
    rationale: str
    rubric_check: str
    brand_check: str


class EvaluatedKeyword(BaseModel):
    """One keyword from the enriched pool, scored on two axes.

    `volume`/`difficulty` come from DataForSEO when available (has_data=True);
    `voice_score` comes from the judge's brand-voice read; `viability_score`
    and `category` are derived deterministically from config thresholds.
    """

    term: str
    volume: int | None = None
    difficulty: int | None = None
    has_data: bool = False
    voice_score: int = 3
    viability_score: int = 3
    note: str = ""
    category: str = ""
    selected: bool = False


class ExecutionPlan(BaseModel):
    page_url: str
    primary_keyword: str
    secondary_keywords: list[str]
    items: list[PlanItem]
    body_changes: list[BodyChange]
    redirect_mapping: str | None = None
    keyword_pool: list[EvaluatedKeyword] = Field(default_factory=list)


class BrandFlag(BaseModel):
    field: str
    status: str  # "ok" or "flag"
    note: str = ""
