"""Render the human-paste-ready output: markdown + Word, field-by-field."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

from docx import Document
from docx.shared import Pt

from .schema import ExecutionPlan, PageSnapshot

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

DEV_MODE_WARNING = (
    "⚠ IMPORTANT: be very careful NOT to toggle Developer Mode while you are in "
    "this area. Only paste the mapping line into URL mappings and save."
)


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.replace("/", "-") or "home"


def _decision(plan: ExecutionPlan, field: str, default: str = "keep current") -> str:
    for item in plan.items:
        if item.field == field:
            return item.decision
    return default



def render_pastepack(plan: ExecutionPlan, snapshot: PageSnapshot, operator: str = "Reviewer") -> dict:
    """Build the paste-pack and save both .md and .docx to output/.

    Returns {"markdown": str, "md_path": str, "docx_path": str}.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo("America/New_York"))
    slug = _slug_from_url(plan.page_url)

    seo_title = _decision(plan, "seo_title")
    meta_description = _decision(plan, "meta_description")
    url_slug = _decision(plan, "url_slug")
    h1 = _decision(plan, "h1")

    page_title = h1 if h1.lower() != "keep current" else (snapshot.h1 or "")

    # Dynamic section counter so numbers stay sequential when redirect is absent.
    sec = 1

    lines: list[str] = []
    lines.append(f"# SEO update: {plan.page_url}")
    lines.append(f"Generated {timestamp:%Y-%m-%d %H:%M} · Reviewed and approved by {operator}")
    lines.append("")
    lines.append(f"## {sec}. Page settings → SEO tab")
    sec += 1
    # Trailing two spaces = Markdown hard line-break so Gradio renders each on its own line.
    lines.append(f"SEO title: {seo_title}  ")
    lines.append(f"SEO description: {meta_description}")
    lines.append("")
    lines.append(f"## {sec}. Page settings → General tab")
    sec += 1
    lines.append(f"URL slug: {url_slug}  ")
    lines.append(f"Page title: {h1}")
    lines.append("")

    if plan.redirect_mapping:
        lines.append(f"## {sec}. URL redirect  (only if URL changed)")
        sec += 1
        lines.append(f"Mapping line: {plan.redirect_mapping}  ")
        lines.append(
            "Where: Settings → look for \"URL mappings\" (under Advanced or "
            "Developer tools, depending on your Squarespace version)."
        )
        lines.append(DEV_MODE_WARNING)
        lines.append("")

    lines.append(f"## {sec}. Page content changes (in the Squarespace editor)")
    sec += 1
    for i, bc in enumerate(plan.body_changes, start=1):
        action_label = bc.action.replace("remove_section", "remove").replace("add_section", "add")
        lines.append(f"{i}. [{action_label}]  ")
        lines.append(f"   Instruction: {bc.instruction}  ")
        if bc.new_text:
            lines.append(f"   New text: {bc.new_text}")
    lines.append("")

    lines.append(f"## {sec}. Google AdWords keywords")
    sec += 1
    for kw in plan.keyword_pool:
        lines.append(f"- {kw.term}")
    lines.append("")

    markdown = "\n".join(lines)

    md_path = OUTPUT_DIR / f"{slug}_{timestamp:%Y%m%d}.md"
    md_path.write_text(markdown)

    docx_path = OUTPUT_DIR / f"{slug}_{timestamp:%Y%m%d}.docx"
    _write_docx(docx_path, plan, snapshot, operator, timestamp, seo_title, meta_description, url_slug, h1)

    return {"markdown": markdown, "md_path": str(md_path), "docx_path": str(docx_path)}


def _write_docx(
    path: Path,
    plan: ExecutionPlan,
    snapshot: PageSnapshot,
    operator: str,
    timestamp: datetime,
    seo_title: str,
    meta_description: str,
    url_slug: str,
    h1: str,
) -> None:
    sec = 1

    doc = Document()
    doc.add_heading(f"SEO update: {plan.page_url}", level=1)
    doc.add_paragraph(f"Generated {timestamp:%Y-%m-%d %H:%M} · Reviewed and approved by {operator}")

    doc.add_heading(f"{sec}. Page settings → SEO tab", level=2)
    sec += 1
    doc.add_paragraph(f"SEO title: {seo_title}")
    doc.add_paragraph(f"SEO description: {meta_description}")

    doc.add_heading(f"{sec}. Page settings → General tab", level=2)
    sec += 1
    doc.add_paragraph(f"URL slug: {url_slug}")
    doc.add_paragraph(f"Page title: {h1}")

    if plan.redirect_mapping:
        doc.add_heading(f"{sec}. URL redirect (only if URL changed)", level=2)
        sec += 1
        doc.add_paragraph(f"Mapping line: {plan.redirect_mapping}")
        doc.add_paragraph(
            "Where: Settings → look for \"URL mappings\" (under Advanced or "
            "Developer tools, depending on your Squarespace version)."
        )
        warn = doc.add_paragraph()
        run = warn.add_run(DEV_MODE_WARNING)
        run.bold = True
        run.font.size = Pt(11)

    doc.add_heading(f"{sec}. Page content changes (in the Squarespace editor)", level=2)
    sec += 1
    for i, bc in enumerate(plan.body_changes, start=1):
        action_label = bc.action.replace("remove_section", "remove").replace("add_section", "add")
        doc.add_paragraph(f"[{action_label}]", style="List Number")
        doc.add_paragraph(f"Instruction: {bc.instruction}")
        if bc.new_text:
            doc.add_paragraph(f"New text: {bc.new_text}")

    doc.add_heading(f"{sec}. Google AdWords keywords", level=2)
    sec += 1
    for kw in plan.keyword_pool:
        doc.add_paragraph(kw.term, style="List Bullet")

    doc.save(path)
