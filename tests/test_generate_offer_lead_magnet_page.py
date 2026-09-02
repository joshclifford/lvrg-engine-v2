"""generate_offer_lead_magnet_page must route get_listed/sponsored_story to
their own prompt content (not the Free Website prompt), thread the vertical
framing for get_listed, and reject an unknown offer rather than silently
falling back to some default lead magnet type."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

import generator


def _intel(**overrides):
    base = {
        "business_name": "Dark Horse Coffee Roasters",
        "business_type": "cafe",
        "domain": "darkhorsecoffeeroasters.com",
        "description": "Small-batch coffee roaster in Normal Heights.",
        "services": ["Coffee subscriptions", "Wholesale"],
        "location": "San Diego, CA",
        "neighborhood": "Normal Heights",
        "phone": "555-1234",
        "hours": "Mon-Sun 7-5",
        "brand_vibe": "warm, artisanal",
        "primary_color": "#7a4a2b",
        "photos": [],
        "rating": None,
        "review_count": None,
        "press_mentions": [],
        "socials": {},
    }
    base.update(overrides)
    return base


def _mock_client(captured, html="<!DOCTYPE html><html><head></head><body>hi</body></html>"):
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=SimpleNamespace(
        get_final_message=lambda: SimpleNamespace(
            stop_reason="end_turn",
            # Thinking block first — see the note in test_generate_page.py.
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


def _prompt_text(captured):
    return captured[0]["messages"][0]["content"]


def test_get_listed_realtor_prompt_mentions_realtor_framing_and_297(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("get_listed", _intel(), vertical="realtor")

    prompt = _prompt_text(captured)
    assert "real estate agent" in prompt
    assert "$297" in prompt
    assert "ThereSanDiego.com" in prompt


def test_get_listed_contractor_gets_contractor_framing_not_realtor(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("get_listed", _intel(), vertical="contractor")

    prompt = _prompt_text(captured)
    assert "independent contractor" in prompt
    assert "real estate agent" not in prompt


def test_get_listed_unknown_vertical_degrades_to_generic_framing_not_a_crash(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("get_listed", _intel(), vertical="veterinarian")

    prompt = _prompt_text(captured)
    assert "local business" in prompt


def test_sponsored_story_prompt_mentions_editorial_voice_and_first_look(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "editorial" in prompt
    assert "First Look" in prompt
    assert "$197" in prompt
    assert "guaranteed impressions" in prompt


def test_unknown_offer_raises_instead_of_silently_defaulting(monkeypatch):
    with pytest.raises(ValueError):
        generator.generate_offer_lead_magnet_page("get_rich_quick", _intel())


def test_no_nav_or_multi_page_language_since_this_is_a_standalone_page(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "no nav to other pages" in prompt.lower()
