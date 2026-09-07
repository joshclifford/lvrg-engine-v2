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


def test_sponsored_story_prompt_mentions_editorial_voice_and_real_plan_pricing(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "editorial" in prompt
    assert "guaranteed impressions" in prompt
    assert "$497/month" in prompt
    assert "$997/month" in prompt
    assert "$1,500/month" in prompt


def test_sponsored_story_never_quotes_the_first_look_social_price(monkeypatch):
    """First Look is a $197 SOCIAL plan, not a Sponsored Story. Quoting it here
    anchored prospects ~30x under the real $497/month ask."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "$197" not in prompt
    assert "First Look" not in prompt


def test_get_listed_sells_delivery_window_and_upgrade_credit(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("get_listed", _intel(), vertical="restaurant")

    prompt = _prompt_text(captured)
    assert "5 business days" in prompt
    assert "local restaurant" in prompt


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_both_offers_ban_em_dashes_in_generated_copy(monkeypatch, offer):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(offer, _intel())

    prompt = _prompt_text(captured)
    assert "NO EM-DASHES" in prompt


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_both_offers_receive_cta_angle_and_pain_point(monkeypatch, offer):
    """Both were previously dropped on the floor here despite the scraper
    populating them and every other generator using them."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    intel = _intel()
    intel["cta_angle"] = "Book a table"
    intel["pain_point"] = "no online booking"
    generator.generate_offer_lead_magnet_page(offer, intel)

    prompt = _prompt_text(captured)
    assert "Book a table" in prompt
    assert "no online booking" in prompt


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_generated_html_contains_no_em_dashes_even_when_the_model_emits_them(monkeypatch, offer):
    """The prompt rule asks. This proves the output is stripped regardless."""
    dirty = (
        "<!DOCTYPE html><html><body>"
        "<h1>Colepepper Plumbing — San Diego</h1>"
        "<p>Open 9—5. Trusted since 2006 &mdash; three generations.</p>"
        "<p>Serving Carlsbad &#8212; Chula Vista, 10–20 miles out.</p>"
        "</body></html>"
    )
    captured = []
    monkeypatch.setattr(
        generator, "_get_client", lambda **k: _mock_client(captured, html=dirty)
    )

    html = generator.generate_offer_lead_magnet_page(offer, _intel())

    for bad in ("—", "–", "&mdash;", "&ndash;", "&#8212;", "&#8211;"):
        assert bad not in html, f"{bad!r} survived into the generated page"


def test_every_advertised_number_matches_the_live_tsd_funnel(monkeypatch):
    """Pins the offer copy to advertise.theresandiego.com, re-verified 7 Sep 2026.

    Sources: /story-plans for the tiers, / for the audience figures,
    /business-profile-checkout for the $297. If TSD changes a price, this test
    should fail and be updated deliberately, not drift quietly.
    """
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())
    prompt = _prompt_text(captured)

    for tier, price, impressions in (
        ("LOCAL", "$497/month", "10,000"),
        ("CITYWIDE", "$997/month", "25,000"),
        ("COUNTYWIDE", "$1,500/month", "50,000"),
    ):
        assert tier in prompt
        assert price in prompt
        assert impressions in prompt

    # Audience figures, verbatim from the funnel homepage.
    for stat in ("70,000+", "700,000+", "25,000", "82,000"):
        assert stat in prompt

    captured.clear()
    generator.generate_offer_lead_magnet_page("get_listed", _intel())
    assert "$297" in _prompt_text(captured)


def test_number_ranges_become_hyphens_not_commas():
    assert generator._strip_em_dashes("Open 9—5 daily") == "Open 9-5 daily"
    assert generator._strip_em_dashes("10–20 miles") == "10-20 miles"


def test_prose_dashes_become_commas_without_doubling_punctuation():
    assert generator._strip_em_dashes("Kickserv — since 2006") == "Kickserv, since 2006"
    # A dash straight after a full stop must not leave ". ,"
    assert generator._strip_em_dashes("Done. — Next") == "Done. Next"


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_neither_offer_asks_for_testimonial_quotes(monkeypatch, offer):
    """intel["reviews"] is never populated, so a quote section could only be
    filled by inventing one. Rating stats are fine; quotes are not."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(offer, _intel())

    prompt = _prompt_text(captured)
    assert "PULL QUOTE" not in prompt
    assert "TESTIMONIALS" not in prompt


def test_unknown_offer_raises_instead_of_silently_defaulting(monkeypatch):
    with pytest.raises(ValueError):
        generator.generate_offer_lead_magnet_page("get_rich_quick", _intel())


def test_no_nav_or_multi_page_language_since_this_is_a_standalone_page(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "no nav to other pages" in prompt.lower()
