# SEO Workflow — Human-in-the-Loop Demo

## What it does

This is a proof-of-concept that automates the "generate → judge → format" core
of a manual, multi-chatbot SEO workflow for a ~1,000-page Squarespace site.
It fetches a live page, sends it to 2-3 frontier models for SEO
recommendations, has a judge model synthesize those into a single execution
plan, runs a brand-voice check, and lets a human review and edit everything
before producing a copy-paste-ready output and updating the work-queue
spreadsheet.

## The workflow it automates

Today, the client does this by hand: for each page, he feeds the page content
to ChatGPT, Claude, and Genspark for SEO suggestions, combines those into one
document, feeds that to Gemini for a final synthesis (H1/URL/keyword/content
decisions), manually applies the changes in the Squarespace editor, sets up a
301 redirect if the URL changed (being careful never to toggle Developer
Mode), and logs the old → new URL mapping and new page title in a tracking
spreadsheet. This takes roughly 30-45 minutes per page across ~1,000 pages.
This tool keeps every decision point but automates the mechanical assembly,
turning that into a ~3-5 minute review-and-approve loop per page.

## Architecture

```
                 ┌────────────────────┐
                 │  workbook queue     │  (URLs to Do, opportunity-ranked)
                 └─────────┬───────────┘
                            │ pick a page
                            ▼
                 ┌────────────────────┐
                 │  page_fetcher       │  fetch + parse live page,
                 │  (fetch + parse)    │  detect legacy sections
                 └─────────┬───────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
       generator A      generator B     generator C   (parallel, ~10-15
            └───────────────┼───────────────┘         keywords each)
                            ▼
                 ┌────────────────────┐
                 │  keyword enrichment │  pool + dedupe candidates,
                 │  (DataForSEO)       │  one bulk request for volume/
                 │                     │  difficulty + related terms
                 └─────────┬───────────┘  (skipped gracefully if not
                            ▼              configured)
                 ┌────────────────────┐
                 │  judge (synthesis) │  + two-axis keyword scoring
                 │                    │  + mechanical rubric validation
                 │                    │  + standard-section enforcement
                 └─────────┬───────────┘
                            ▼
                 ┌────────────────────┐
                 │  brand-fit verify  │  separate call, voice guardian
                 └─────────┬───────────┘
                            ▼
                 ┌────────────────────┐
                 │  HUMAN REVIEW       │  edit any field, see live
                 │  (Gradio UI)        │  char counters + voice flags
                 └─────────┬───────────┘
                            │ approve
                            ▼
                 ┌────────────────────┐
                 │  paste-pack (.md/  │  + updated workbook copy
                 │  .docx) + registry │  + master URL/title list
                 └────────────────────┘
```

## Setup & Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in at least ANTHROPIC_API_KEY
python -m src.app
```

## Keyword enrichment (DataForSEO)

The judge no longer guesses keyword viability — it picks final keywords from
a pool grounded in real search-volume and keyword-difficulty data.

**How it works:**
1. Each generator model proposes a wide pool of candidate keywords (~10-15,
   configurable in `config/dataforseo.yaml`), based purely on semantic and
   brand-voice fit — ignoring volume/difficulty at this stage.
2. All candidates are pooled and deduplicated, then sent to DataForSEO in a
   single bulk request for search volume and keyword difficulty, plus a bulk
   request for related/suggested keywords to widen the pool further.
3. The judge receives this grounded pool (every candidate + related term,
   each annotated with volume/difficulty, or "no data" if DataForSEO has
   none) and must choose `primary_keyword`/`secondary_keywords` ONLY from it.
4. Every keyword in the pool is scored on two axes and exposed for review:
   - **Voice fit (1-5)** — the judge's read on how well it matches the
     brand's contemplative/literary voice.
   - **SEO viability (1-5)** — derived deterministically from
     difficulty/volume against the thresholds in `config/dataforseo.yaml`
     (low difficulty = winnable for a low-authority site, high difficulty
     is penalized, volume is a tiebreaker, not a gate).

**Tuning:** difficulty cutoffs, the minimum-volume rule, the voice-flag
threshold, category labels, and the DataForSEO locale (location/language
codes) are all in `config/dataforseo.yaml` — edit and re-run, no code
changes needed. Treat the starting values as hypotheses; after the first
live calls, eyeball whether difficulty looks sane for this niche and retune.

**Gatekeeping, not deletion:** keywords below the voice-fit threshold are
labeled with a "(flagged)" category and de-prioritized in the review table,
but never deleted — a low voice score is a subjective LLM judgment, and the
human reviewer keeps final say by editing the Primary/Secondary keyword
fields directly.

**Sandbox vs. live:** `config/dataforseo.yaml` has a `mode` field —
`"sandbox"` (default) uses DataForSEO's free sandbox endpoints (dummy data,
no cost) so you can build and test the whole flow before spending real
credits. Set `mode: "live"` once you're ready, and set a daily spend cap in
the DataForSEO dashboard first.

**Graceful degradation:** with no `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD`
set (see `.env.example`), the app behaves exactly as before — keyword
suggestions are ungrounded, and the review UI shows "Keyword data: not
connected". If a DataForSEO call fails or times out, the run still completes
with ungrounded keywords and the UI shows "enrichment failed" — it never
crashes the pipeline. Note that volume/difficulty figures are estimates and
are least reliable at very low search volumes.

Headless / scriptable mode (no Gradio, proves the pipeline is a plain Python
module):

```bash
python -m src.pipeline https://herondance.org/favorite-joseph-campbell-quotes
```

## POC boundaries and the production path

This POC intentionally stops short of several things that would be needed for
ongoing, self-serve use:

- **Query-level keyword data** is currently an optional manual CSV/xlsx
  upload (the standard GSC "Queries" export). In production this would be
  fetched automatically per-page via the Google Search Console API
  (OAuth, read-only), removing the manual export step entirely.
- **Batch mode**: this demo reviews one page at a time. A production version
  would let the operator queue N pages and step through them sequentially,
  reusing the same review UI.
- **Hosted deployment**: running this for the client to use themselves would
  mean deploying it (with their API keys) rather than running it locally.
- **Squarespace remains manual-paste by design.** There is no Squarespace
  content API, and browser automation against the editor was deliberately
  rejected — it's fragile, hard to audit, and erodes the trust this workflow
  depends on. The paste-pack format exists specifically to make manual
  application fast and low-error.
- **Registry**: the "URLs to Do" / "URLs Done" workbook could move to a
  shared Google Sheet so multiple people can see progress in real time.
- **Master URL + title list**: this is regenerated after every approval and
  is explicitly meant to feed the client's next project — adding a standard
  cross-link section to every content page once all pages have been updated.
