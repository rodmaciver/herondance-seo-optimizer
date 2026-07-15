"""The redirect_mapping field must always come out in Squarespace format."""
from src.schema import ExecutionPlan


def _plan(redirect):
    return ExecutionPlan(
        page_url="https://herondance.org/x",
        primary_keyword="k",
        secondary_keywords=[],
        items=[],
        body_changes=[],
        redirect_mapping=redirect,
    )


def test_adds_missing_slashes():
    p = _plan("big-me-little-me -> big-me-little-me-subconscious 301")
    assert p.redirect_mapping == "/big-me-little-me -> /big-me-little-me-subconscious 301"


def test_keeps_correct_format_unchanged():
    p = _plan("/old-page -> /new-page 301")
    assert p.redirect_mapping == "/old-page -> /new-page 301"


def test_adds_missing_301():
    p = _plan("old-page -> new-page")
    assert p.redirect_mapping == "/old-page -> /new-page 301"


def test_strips_domain():
    p = _plan("https://herondance.org/old-page -> https://herondance.org/new-page 301")
    assert p.redirect_mapping == "/old-page -> /new-page 301"


def test_unicode_arrow_and_whitespace():
    p = _plan("  old-page  →  new-page   301 ")
    assert p.redirect_mapping == "/old-page -> /new-page 301"


def test_null_like_values_become_none():
    for v in (None, "", "null", "None", "N/A", "  "):
        assert _plan(v).redirect_mapping is None


def test_trailing_slash_stripped():
    p = _plan("old-page/ -> new-page/ 301")
    assert p.redirect_mapping == "/old-page -> /new-page 301"
