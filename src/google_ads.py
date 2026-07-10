"""Google Ads RSA copy generation from an existing SEO plan."""
from __future__ import annotations

import logging
import re
from pydantic import BaseModel

log = logging.getLogger(__name__)

from .model_clients import call_model
from .page_fetcher import PageSnapshot
from .schema import ExecutionPlan

MAX_ATTEMPTS = 5

_SYSTEM = """You are one of the world's leading experts in Google Ads with a deep sensitivity to a narrow, contemplative audience that is suspicious of marketing.

Generate Google Ads Section 5 output for Heron Dance / Zen Mountain Journal — a contemplative artist-publisher's website featuring art, writing, and reflection inspired by Zen and Taoist poetry, wilderness solitude, and inner quiet. The audience is a narrow, deeply engaged segment interested in Zen, Taoist, and contemplative subjects. Generic advertising language disqualifies the copy. The qualifier "where likely or very possible to contribute positively to results" applies to every generation task.

VOICE: Contemplative, plain, declarative. Reads as a librarian's quiet content recommendation.

HEADLINE GROUNDING RULE (mandatory — every headline without exception):

Every headline must contain at least one of:
- The words Zen, Tao, or Taoist
- A named person, text, or concept from the Zen or Taoist tradition (e.g., Chuang Tzu, Li Bai, Wang Wei, Han Shan, wu wei, Tao Te Ching)
- A page-specific named entity that carries clear Zen or Taoist identity
- The ad group's own keyword vocabulary
- A plain factual page-content description
- A brand line such as Zen Mountain Journal or Art and Essays by Rod MacIver

"Solitude" alone fails this rule. "Zen solitude" or "Taoist solitude" passes.
Do not invent claims or make the line promotional. The headline should read like
a chapter title or museum label, not an advertisement.

BANNED VOCABULARY (any occurrence triggers regeneration):

Imperative verbs: Discover, Explore, Unlock, Find Out, Find Your, Learn How, Get, Get Started, Transform, Begin, Awaken, See How, See Why, Experience, Master, Embrace, Embark.

Urgency words: Now, Today, Here (as urgency), Limited Time, Don't Miss, Hurry, Act Fast, Last Chance, While Supplies Last.

Superlatives: Best, Top, #1, Greatest, Ultimate, Essential, Must-Read, Must-Have, Amazing, Incredible, Breathtaking, Stunning, Revolutionary, Groundbreaking, Life-Changing, Exclusive, Premium, Elite, VIP.

Generic spiritual marketing: Discover Yourself, Find Yourself, Transform Your Life, Change Your Life.

Possessive promises: "Your Path to" anything, "Your Journey" anything, "Your Guide to" anything.

Unsupported claims: Official, Guaranteed, Free, Cure, Proven, #1 Rated.

Formatting: Exclamation points, ALL CAPS words (except recognized acronyms), trailing ellipses.

Note on profound / powerful / enlightenment / eternal wisdom: Acceptable only when directly paired with a Zen or Tao modifier (e.g., "Zen enlightenment", "Taoist wisdom"). Banned when used alone or in a generic spiritual context.

PREFERRED HEADLINE PATTERNS:
- Person + Identifier: "Han Shan, Cold Mountain Poet"
- Subject + Quiet Framing: "Reflections on Cold Mountain"
- Category Description: "Tang Dynasty Hermit Poetry"
- Direct Topic Statement: "The Unknown Craftsman"

All headlines must also satisfy the grounding rule above.

PREFERRED DESCRIPTION PATTERNS:
- Content + Voice Credibility: "Essays on Han Shan's poetry. From a contemplative artist."
- Specific Content Description: "Favorite passages from Yanagi's Unknown Craftsman."
- Acknowledgment of Reader's Interest: "For readers drawn to the quiet poets of Tang China."

CAMPAIGN-LEVEL NEGATIVES ALREADY IN PLACE — do not suggest these as page-specific negatives:

These terms are blocked at campaign level and do not need to appear in your output. Your page-specific negatives must be terms that would attract irrelevant traffic based on the content of this specific page only.

Generic (always excluded): amazon, ebay, free, images, lesson plan, pdf, printable, reddit, summary, tattoo, translation only, wallpaper, wikipedia, worksheet, official, guaranteed, cure, proven.

Brand (always excluded): heron dance, heron dance art studio, heron dance org, herondance, herondance.org, zen mountain journal, zen mountain journal org, zenmountainjournal, zenmountainjournal.org, roderick maciver, rod maciver, roderickmaciver, rodmaciver, roderick maciver arts, roderickmaciverarts, roderick maciver artist, roderick maciver art, roderick maciver paintings, roderick maciver prints, roderick maciver books, rod maciver artist, rod maciver art, rod maciver paintings, rod maciver prints, rod maciver books, roderick mciver, rod mciver, roderick mac iver, rod mac iver, maciver art, maciver artist, maciver paintings, maciver prints.

REQUIRED OUTPUT:

- Generate a candidate pool of roughly 20 headlines, then filter for quality. Return up to 12 headlines (max 30 characters each) where likely or very possible to contribute positively to results. Every headline must satisfy the grounding rules. It is better to return fewer strong headlines than padded mediocre ones. Minimum 3 headlines.
- 2 to 4 descriptions (max 90 characters each), targeting 4 where possible, where likely or very possible to contribute positively to results.
- 3 to 8 core phrase-match keywords where likely or very possible to receive searches. Format in quotes.
- For each core keyword, 2 to 4 word-order and minor variants where likely or very possible to contribute positively to results. Format each in brackets.
- 0 to 10 page-specific negative keywords — real-world searches with the WRONG INTENT that this page's keywords might accidentally attract: merchandise the studio does not sell (mugs, t-shirts, phone cases, furniture, coloring pages), travel and outdoor recreation (tours, hiking gear, trail maps), academic study (thesis, citation, course), crafts and how-to, or a different famous namesake. PROTECTED PRODUCTS — the studio sells original paintings, watercolors, archival and canvas prints, framed art, contemplative journals, notecards, and books. NEVER include a negative keyword containing: print, prints, canvas, framed, painting, paintings, watercolor, watercolors, journal, journals, book, books, card, cards, artwork — people searching those terms are potential customers, not irrelevant traffic. A negative keyword must also NEVER be built from this page's own content, imagery, or vocabulary — words and images that appear on the page belong to the readers we WANT, so blocking them turns away our own audience. Every negative must contain at least one word that does not appear anywhere on the page. Do not include any term already listed in the campaign-level negatives above. If no genuinely wrong-intent terms exist for this page, return an empty list — an empty list is a correct and welcome answer; never invent negatives to fill a quota.
- At least one specific named entity (person, book, concept, or place) from the page must appear in headlines or descriptions.

USE PAGE-SPECIFIC TERMS: Final SEO title, page title, headings, named writers, named poems, named books, translators, distinctive phrases, narrow Taoist/Zen/contemplative concepts.

USE WHEN APPLICABLE: Zen Mountain Journal or The Tao Te Ching Journal when the final page includes a related product, newsletter, callout, or internal link. NEVER use the retired name "Heron Dance Art Studio" in ad copy — the studio is rebranding; brand lines are "Zen Mountain Journal" or "Art and Essays by Rod MacIver".

OUTPUT FORMAT: JSON with keys: headlines, descriptions, core_keywords, keyword_variants, negative_keywords."""

# Simple substring banned words (multi-word phrases and carefully chosen single words).
# Single-word entries use word-boundary matching in _check() to avoid false positives.
BANNED_WORDS = [
    # Imperative verbs
    "discover", "explore", "unlock", "find out", "find your", "learn how",
    "get started", "awaken", "see how", "see why", "experience",
    "embrace", "embark",
    # Urgency / time
    "don't miss", "limited time", "last chance", "act fast", "while supplies last",
    # Superlatives / hype
    "#1", "must-read", "must-have", "life-changing", "#1 rated",
    # Generic spiritual marketing (trimmed per 07/01 client update)
    "discover yourself", "find yourself", "transform your life", "change your life",
    # Possessive promises
    "your path to", "your journey", "your guide to",
    # Unsupported claims
    "official", "guaranteed", "cure", "proven",
]

# Single words that need word-boundary matching to avoid false positives on substrings.
BANNED_WORDS_EXACT = [
    "shop", "buy", "order",
    "transform", "begin", "master", "embark",
    "now", "today", "hurry", "limited", "sale", "deal", "free",
    "best", "top", "greatest", "ultimate", "essential",
    "amazing", "incredible", "breathtaking", "stunning",
    "revolutionary", "groundbreaking", "exclusive", "premium", "elite", "vip",
    "get",
]

BANNED_STARTS = [
    "discover", "explore", "unlock", "find out", "find your", "find ",
    "learn", "get ", "transform", "begin", "awaken",
    "see how", "see why", "experience", "master", "embrace", "embark",
]

# These are only banned without a nearby Zen/Tao modifier.
_CONTEXT_SENSITIVE = ["profound", "powerful", "enlightenment", "eternal wisdom"]
_ZEN_TAO_MODIFIERS = ["zen", "tao", "taoist"]

# Known Zen/Tao anchors for headline validation.
# Intentionally broad; the AI handles page-specific named entities the list may miss.
_ZEN_TAO_ANCHORS = {
    "zen", "tao", "taoist", "taoism",
    "chuang tzu", "zhuangzi", "li bai", "li po", "wang wei",
    "han shan", "cold mountain", "wu wei", "tao te ching",
    "daodejing", "lao tzu", "laozi", "tang dynasty", "tang poet",
    "ikkyu", "basho", "ryokan", "dogen", "shih-shu",
    "tsin", "tsin dynasty", "tsu yeh", "tao yuanming", "tao qian",
}

_PLAIN_FACTUAL_ANCHORS = {
    "art", "artist", "essays", "essay", "reflections", "watercolors",
    "paintings", "poem", "poetry", "journal", "contemplative", "quiet",
    "stillness", "silence", "mountain", "hermit", "companion",
}

_BRAND_ANCHORS = {
    "heron dance", "zen mountain journal", "rod maciver", "roderick maciver",
    "tao te ching journal",
}

_CAMPAIGN_NEGATIVES = {
    "amazon", "ebay", "free", "images", "lesson plan", "pdf", "printable",
    "reddit", "summary", "tattoo", "translation only", "wallpaper", "wikipedia",
    "worksheet", "official", "guaranteed", "cure", "proven", "heron dance",
    "heron dance art studio", "heron dance org", "herondance", "herondance.org",
    "zen mountain journal", "zen mountain journal org", "zenmountainjournal",
    "zenmountainjournal.org", "roderick maciver", "rod maciver", "roderickmaciver",
    "rodmaciver", "roderick maciver arts", "roderickmaciverarts",
    "roderick maciver artist", "roderick maciver art", "roderick maciver paintings",
    "roderick maciver prints", "roderick maciver books", "rod maciver artist",
    "rod maciver art", "rod maciver paintings", "rod maciver prints",
    "rod maciver books", "roderick mciver", "rod mciver", "roderick mac iver",
    "rod mac iver", "maciver art", "maciver artist", "maciver paintings",
    "maciver prints",
}


class _AdAssets(BaseModel):
    headlines: list[str]
    descriptions: list[str]
    core_keywords: list[str]
    keyword_variants: dict[str, list[str]]
    negative_keywords: list[str]


def _word_match(text: str, term: str) -> bool:
    return bool(re.search(r"\b" + re.escape(term) + r"\b", text))


def _extract_page_entities(snapshot: "PageSnapshot") -> set[str]:
    """Return lowercased named-entity candidates from snapshot content."""
    sources = (
        [snapshot.h1 or "", snapshot.title or ""]
        + list(snapshot.headings or [])
        + [(snapshot.body_text or "")[:2000]]
    )
    entities: set[str] = set()
    for text in sources:
        # Grab contiguous runs of Title-cased words (proper nouns)
        for m in re.finditer(r"[A-Z][a-zA-Zéàü'\-]+(?:\s+[A-Z][a-zA-Zéàü'\-]+)*", text):
            phrase = m.group().lower()
            entities.add(phrase)
            for word in phrase.split():
                if len(word) >= 4:  # skip very short words like "Tu", "Fu"
                    entities.add(word)
    return entities


def _has_zen_tao_anchor(headline: str, page_entities: set[str] | None = None) -> bool:
    h = headline.lower()
    if any(anchor in h for anchor in _ZEN_TAO_ANCHORS):
        return True
    if any(anchor in h for anchor in _BRAND_ANCHORS):
        return True
    if any(_word_match(h, anchor) for anchor in _PLAIN_FACTUAL_ANCHORS):
        return True
    if page_entities and any(entity in h for entity in page_entities):
        return True
    return False


def _dedupe_key(value: str) -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r"[a-z0-9]+", value.casefold()))))


_NEGATIVE_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "with",
    "by", "at", "from", "as", "is", "are", "how", "what", "who",
}


def _stem(word: str) -> str:
    """Crude stem so 'settling' matches 'settles': trim common suffixes."""
    for suffix in ("ing", "ers", "er", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _extract_page_words(snapshot: "PageSnapshot") -> set[str]:
    """Return stemmed lowercase words from the page's visible content."""
    sources = (
        [snapshot.h1 or "", snapshot.title or "", snapshot.meta_description or ""]
        + list(snapshot.headings or [])
        + [snapshot.body_text or ""]
    )
    words: set[str] = set()
    for text in sources:
        for token in re.findall(r"[a-zA-Zéàü'\-]+", text.lower()):
            words.add(_stem(token))
    return words


# Products the studio sells; negatives containing these would block customers.
_PROTECTED_PRODUCT_TERMS = {
    "print", "prints", "canvas", "framed", "painting", "paintings",
    "watercolor", "watercolors", "journal", "journals", "book", "books",
    "card", "cards", "notecard", "notecards", "artwork",
}


def _protected_product_negatives(neg_kws: list[str]) -> list[str]:
    """Return negatives that would block searches for products the studio sells."""
    protected_stems = {_stem(w) for w in _PROTECTED_PRODUCT_TERMS}
    hits = []
    for neg in neg_kws:
        tokens = re.findall(r"[a-zA-Zéàü'\-]+", str(neg).lower())
        if any(_stem(t) in protected_stems for t in tokens):
            hits.append(str(neg))
    return hits


def _page_derived_negatives(neg_kws: list[str], page_words: set[str]) -> list[str]:
    """Return negatives whose every content word appears on the page.

    A negative built entirely from the page's own vocabulary is the signature
    of an imagery-derived negative (e.g. 'mud settling' on a Tao Te Ching
    chapter 15 page) and would block the page's own audience.
    """
    derived = []
    for neg in neg_kws:
        content_words = [
            _stem(w)
            for w in re.findall(r"[a-zA-Zéàü'\-]+", str(neg).lower())
            if w not in _NEGATIVE_STOPWORDS
        ]
        if content_words and all(w in page_words for w in content_words):
            derived.append(str(neg))
    return derived


def _has_all_caps_word(value: str) -> bool:
    return any(
        token.isupper() and len(token) > 1 and token not in {"SEO", "RSA", "CPC"}
        for token in re.findall(r"[A-Za-z0-9#]+", value)
    )


def _context_violations(text: str) -> list[str]:
    """Return context-sensitive words present without a nearby Zen/Tao modifier."""
    violations = []
    text_lower = text.lower()
    tokens = text_lower.split()
    for word in _CONTEXT_SENSITIVE:
        if word not in text_lower:
            continue
        for i, token in enumerate(tokens):
            # Match single-word and two-word phrases
            chunk = token if " " not in word else " ".join(tokens[i:i + len(word.split())])
            if word in chunk:
                window = " ".join(tokens[max(0, i - 3): i + 4])
                if not any(mod in window for mod in _ZEN_TAO_MODIFIERS):
                    violations.append(word)
                break
    return violations


def _sanitize_negatives(
    assets: dict,
    page_words: set[str] | None = None,
) -> list[str]:
    """Silently drop negative keywords that are unsafe but safely omittable.

    Four categories are removed rather than sent back to the model for a
    retry, because omitting a negative keyword never harms a campaign:
    page-derived negatives (would block the page's own audience),
    negatives naming products the studio sells, repeats of campaign-level
    negatives, and negatives that overlap a positive keyword. Returns the
    list of dropped terms for logging.
    """
    neg_kws = assets.get("negative_keywords", [])
    if not neg_kws:
        return []

    drop: set[str] = set()
    drop.update(str(v) for v in _protected_product_negatives(neg_kws))
    if page_words:
        drop.update(str(v) for v in _page_derived_negatives(neg_kws, page_words))

    core_kws = assets.get("core_keywords", [])
    variants_map = assets.get("keyword_variants", {})
    positive_terms: set[str] = set()
    for core in core_kws:
        cleaned = str(core).strip().strip("[]\"").strip()
        if cleaned:
            positive_terms.add(cleaned.casefold())
        variants = variants_map.get(core, variants_map.get(cleaned, []))
        positive_terms.update(
            str(value).strip().strip("[]\"").strip().casefold()
            for value in variants
            if str(value).strip().strip("[]\"").strip()
        )
    for neg in neg_kws:
        cleaned = str(neg).strip().strip("[]\"").strip().casefold()
        if cleaned in _CAMPAIGN_NEGATIVES or cleaned in positive_terms:
            drop.add(str(neg))

    if drop:
        assets["negative_keywords"] = [n for n in neg_kws if str(n) not in drop]
    return sorted(drop)


def _check(
    assets: dict,
    page_entities: set[str] | None = None,
    page_words: set[str] | None = None,
) -> list[str]:
    """Return violation messages; empty list means clean."""
    failures = []
    headlines = assets.get("headlines", [])
    descriptions = assets.get("descriptions", [])
    core_kws = assets.get("core_keywords", [])
    variants_map = assets.get("keyword_variants", {})
    neg_kws = assets.get("negative_keywords", [])

    if not (3 <= len(headlines) <= 12):
        failures.append(f"Need 3–12 headlines, got {len(headlines)}")
    if not (2 <= len(descriptions) <= 4):
        failures.append(f"Need 2–4 descriptions, got {len(descriptions)}")
    if not (3 <= len(core_kws) <= 8):
        failures.append(f"Need 3–8 core keywords, got {len(core_kws)}")
    if len(neg_kws) > 10:
        failures.append(f"Max 10 page-specific negative keywords, got {len(neg_kws)}")

    for core in core_kws:
        cleaned = str(core).strip().strip("[]\"").strip()
        variants = variants_map.get(core, variants_map.get(cleaned, []))
        if not (2 <= len(variants) <= 4):
            failures.append(
                f"Core keyword {core!r} needs 2–4 variants, got {len(variants)}"
            )

    positive_terms: set[str] = set()
    for core in core_kws:
        cleaned = str(core).strip().strip("[]\"").strip()
        if cleaned:
            positive_terms.add(cleaned.casefold())
        variants = variants_map.get(core, variants_map.get(cleaned, []))
        positive_terms.update(
            str(value).strip().strip("[]\"").strip().casefold()
            for value in variants
            if str(value).strip().strip("[]\"").strip()
        )
    negative_terms = {
        str(value).strip().strip("[]\"").strip().casefold()
        for value in neg_kws
        if str(value).strip().strip("[]\"").strip()
    }
    overlap = positive_terms & negative_terms
    if overlap:
        failures.append(
            "Keywords cannot be both positive and negative: " + ", ".join(sorted(overlap))
        )
    repeated_campaign_negatives = negative_terms & _CAMPAIGN_NEGATIVES
    if repeated_campaign_negatives:
        failures.append(
            "Page negatives repeat campaign negatives: "
            + ", ".join(sorted(repeated_campaign_negatives))
        )
    protected_hits = _protected_product_negatives(neg_kws)
    if protected_hits:
        failures.append(
            "Negative keywords would block searches for products the studio "
            "sells (prints, paintings, journals, books, cards — these "
            "searchers are potential customers; remove them, do not replace "
            "with other product terms): " + ", ".join(sorted(protected_hits))
        )
    if page_words:
        derived = _page_derived_negatives(neg_kws, page_words)
        if derived:
            failures.append(
                "Negative keywords built from the page's own content/imagery "
                "(these would block the page's own audience — replace with "
                "wrong-intent terms or omit): " + ", ".join(sorted(derived))
            )

    for i, h in enumerate(headlines, 1):
        if len(h) > 30:
            failures.append(f"Headline {i} is {len(h)} chars (max 30): '{h}'")
        if not _has_zen_tao_anchor(h, page_entities):
            failures.append(f"Headline {i} missing required grounding: '{h}'")
        if "!" in h:
            failures.append(f"Headline {i} contains an exclamation point: '{h}'")
        if "?" in h:
            failures.append(f"Headline {i} uses a question hook: '{h}'")
        if _has_all_caps_word(h):
            failures.append(f"Headline {i} contains an all-caps word: '{h}'")

    for i, d in enumerate(descriptions, 1):
        if len(d) > 90:
            failures.append(f"Description {i} is {len(d)} chars (max 90): '{d}'")
        if "!" in d:
            failures.append(f"Description {i} contains an exclamation point: '{d}'")
        if _has_all_caps_word(d):
            failures.append(f"Description {i} contains an all-caps word: '{d}'")

    headline_groups: dict[tuple[str, ...], list[str]] = {}
    for value in headlines:
        key = _dedupe_key(value)
        if key:
            headline_groups.setdefault(key, []).append(value)
    for group in headline_groups.values():
        if len(group) > 1:
            failures.append(
                "Near-duplicate headlines — same words reordered; keep one and "
                "rewrite the rest with different vocabulary: "
                + " / ".join(f"'{v}'" for v in group)
            )
    description_groups: dict[str, list[str]] = {}
    for value in descriptions:
        key = value.casefold().strip()
        if key:
            description_groups.setdefault(key, []).append(value)
    for group in description_groups.values():
        if len(group) > 1:
            failures.append(
                "Duplicate descriptions — keep one and rewrite the rest: "
                + " / ".join(f"'{v}'" for v in group)
            )

    for i, text in enumerate(headlines + descriptions, 1):
        if "heron dance art studio" in text.lower():
            failures.append(
                "Uses retired brand name 'Heron Dance Art Studio' — use "
                f"'Zen Mountain Journal' instead: '{text}'"
            )

    all_text = " ".join(headlines + descriptions).lower()
    for banned in BANNED_WORDS:
        if banned in all_text:
            failures.append(f"Contains banned phrase: '{banned}'")
    for banned in BANNED_WORDS_EXACT:
        if _word_match(all_text, banned):
            failures.append(f"Contains banned word: '{banned}'")

    seen_violations: set[str] = set()
    for text in headlines + descriptions:
        for v in _context_violations(text):
            if v not in seen_violations:
                failures.append(f"'{v}' used without Zen/Tao modifier")
                seen_violations.add(v)

    for i, h in enumerate(headlines, 1):
        h_lower = h.lower()
        for start in BANNED_STARTS:
            if h_lower.startswith(start):
                failures.append(f"Headline {i} starts with banned pattern: '{start}'")
                break

    return failures


def generate_ad_assets(
    snapshot: PageSnapshot,
    plan: ExecutionPlan,
    model_config_id: str = "claude",
) -> dict:
    """Generate Google Ads RSA assets for a page.

    Returns dict with keys: headlines, descriptions, core_keywords,
    keyword_variants, negative_keywords, flagged, flag_reason, attempts.
    """
    keywords = [kw.term for kw in plan.keyword_pool]

    user = (
        f"Page URL: {snapshot.url}\n"
        f"H1 / Page title: {snapshot.h1 or 'n/a'}\n"
        f"SEO title: {snapshot.title or 'n/a'}\n"
        f"Meta description: {snapshot.meta_description or 'n/a'}\n"
        f"SEO keywords from analysis: {', '.join(keywords[:15]) or 'n/a'}\n\n"
        f"Page content excerpt:\n{(snapshot.body_text or '')[:800]}\n\n"
        "Generate Google Ads Section 5 output for this page following all rules above."
    )

    last_failures: list[str] = []
    last_assets: dict = {}
    page_entities = _extract_page_entities(snapshot)
    page_words = _extract_page_words(snapshot)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        user_prompt = user
        if last_failures:
            user_prompt += (
                "\n\nPREVIOUS ATTEMPT FAILED — fix only these issues:\n"
                + "\n".join(f"- {f}" for f in last_failures)
            )

        try:
            result = call_model(model_config_id, _SYSTEM, user_prompt, _AdAssets, temperature=0.7)
            last_assets = result
            dropped = _sanitize_negatives(result, page_words)
            if dropped:
                log.info("Dropped unsafe negative keywords: %s", ", ".join(dropped))
            last_failures = _check(result, page_entities, page_words)
            if not last_failures:
                return {
                    "headlines": result["headlines"],
                    "descriptions": result["descriptions"],
                    "core_keywords": result["core_keywords"],
                    "keyword_variants": result["keyword_variants"],
                    "negative_keywords": result["negative_keywords"],
                    "flagged": False,
                    "flag_reason": "",
                    "attempts": attempt,
                }
        except Exception as exc:
            last_failures = [str(exc)]

    return {
        "headlines": last_assets.get("headlines", []),
        "descriptions": last_assets.get("descriptions", []),
        "core_keywords": last_assets.get("core_keywords", []),
        "keyword_variants": last_assets.get("keyword_variants", {}),
        "negative_keywords": last_assets.get("negative_keywords", []),
        "flagged": True,
        "flag_reason": "; ".join(last_failures),
        "attempts": MAX_ATTEMPTS,
    }
