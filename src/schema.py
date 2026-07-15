"""Pydantic data contracts shared across the pipeline stages."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator


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
    source: str = ""
    rationale: str = ""
    rubric_check: str = ""
    brand_check: str = ""


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

    @field_validator("items", "body_changes", "secondary_keywords", "keyword_pool", mode="before")
    @classmethod
    def _parse_json_string(cls, v: object) -> object:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                from json_repair import repair_json
                return json.loads(repair_json(v))
        return v

    @field_validator("redirect_mapping", mode="before")
    @classmethod
    def _normalize_redirect(cls, v: object) -> object:
        """Mechanically enforce Squarespace redirect format: "/old -> /new 301".

        The judge model is inconsistent about leading slashes, so we never rely
        on the prompt for formatting. Tolerates missing slashes, full URLs,
        a unicode arrow, extra whitespace, and a missing trailing 301.
        """
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s or s.lower() in ("null", "none", "n/a"):
            return None
        s = s.replace("\u2192", "->")
        m = re.match(r"^(.*?)\s*->\s*(.*?)(?:\s+301)?\s*$", s)
        if not m:
            return s  # unrecognized shape: leave untouched rather than guess

        def _path(p: str) -> str:
            p = p.strip().strip("\"'")
            p = re.sub(r"^https?://[^/]+", "", p)  # strip any domain
            if not p.startswith("/"):
                p = "/" + p
            if len(p) > 1:
                p = p.rstrip("/")
            return p

        return f"{_path(m.group(1))} -> {_path(m.group(2))} 301"


class BrandFlag(BaseModel):
    field: str
    status: str  # "ok" or "flag"
    note: str = ""
