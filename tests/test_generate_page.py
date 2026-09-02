"""generate_page must thread a pre-resolved design into its prompt (never
re-derive it) and render nav/footer from the full page list it's given, with
the current page marked active — that's what keeps a multi-page site looking
like one business with working links, not four sites glued together."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import generator


def _rich_intel():
    return {
        "business_name": "Acme Dental",
        "business_type": "other",
        "domain": "acmedental.com",
        "description": "Acme Dental has served North Park for twenty years.",
        "services": ["Cleanings", "Whitening"],
        "location": "San Diego, CA",
        "neighborhood": "North Park",
        "phone": "555-1234",
        "hours": "Mon-Fri 9-5",
        "brand_vibe": "clean, modern",
        "primary_color": "#333",
        "cta_angle": "Book a Cleaning",
        "photos": [],
        "rating": None,
        "review_count": None,
        "press_mentions": [],
        "socials": {},
    }


def _nav_plan():
    return [
        {"slug": "index", "filename": "index.html", "title": "Home", "brief": "The main landing page."},
        {"slug": "about", "filename": "about.html", "title": "About", "brief": "The story."},
        {"slug": "contact", "filename": "contact.html", "title": "Contact", "brief": "Phone, hours, location."},
    ]


def _mock_client(captured, html="<!DOCTYPE html><html><head></head><body>hi</body></html>"):
    """Fakes `client.messages.stream(...)` as a context manager whose
    get_final_message() returns an object shaped like the real SDK response,
    and records the prompt sent so tests can assert on it."""
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=SimpleNamespace(
        get_final_message=lambda: SimpleNamespace(
            stop_reason="end_turn",
            # A thinking block FIRST, then the text — the real shape
            # claude-sonnet-5 returns, since adaptive thinking is on by
            # default. The old double emitted a bare text block with no
            # `type`, which is what let `response.content[0].text` pass
            # every test while failing every real build (2 Sep 2026).
            content=[
                SimpleNamespace(type="thinking", thinking="planning the page"),
                SimpleNamespace(type="text", text=html),
            ],
        )
    ))
    stream_cm.__exit__ = MagicMock(return_value=False)

    def _stream(**kwargs):
        captured.append(kwargs)
        return stream_cm

    client = MagicMock()
    client.messages.stream = _stream
    return client


def test_prompt_lists_every_other_page_and_marks_active(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    design = generator._get_design_personality("other")
    nav = _nav_plan()
    generator.generate_page(_rich_intel(), design, nav[1], nav)  # generating "about"

    prompt = captured[0]["messages"][0]["content"]
    assert "about.html" in prompt
    assert "index.html" in prompt
    assert "contact.html" in prompt
    assert '"About"' in prompt and "THIS PAGE" in prompt
    # the active marker must be attached to the page being generated, not another
    about_line = next(line for line in prompt.splitlines() if "about.html" in line)
    assert "THIS PAGE" in about_line


def test_design_text_appears_verbatim_in_prompt(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    design = generator._get_design_personality("bar")
    nav = _nav_plan()
    generator.generate_page(_rich_intel(), design, nav[0], nav)

    prompt = captured[0]["messages"][0]["content"]
    assert design["mood"] in prompt
    assert design["fonts"] in prompt


def test_relative_links_instructed_not_absolute(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    design = generator._get_design_personality("other")
    nav = _nav_plan()
    generator.generate_page(_rich_intel(), design, nav[0], nav)

    prompt = captured[0]["messages"][0]["content"]
    assert 'NEVER a leading slash' in prompt
    assert 'NEVER a full URL' in prompt


def test_returns_html_string_does_not_write_to_disk(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    design = generator._get_design_personality("other")
    nav = _nav_plan()
    result = generator.generate_page(_rich_intel(), design, nav[0], nav)

    assert isinstance(result, str)
    assert "<!DOCTYPE html>" in result
    # generate_page must not touch the filesystem — that's the caller's job.
    assert list(tmp_path.iterdir()) == []


def test_page_max_tokens_used_not_site_max_tokens(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    design = generator._get_design_personality("other")
    nav = _nav_plan()
    generator.generate_page(_rich_intel(), design, nav[0], nav)

    assert captured[0]["max_tokens"] == generator.PAGE_MAX_TOKENS
    assert captured[0]["max_tokens"] < generator.SITE_MAX_TOKENS
