"""Every CTA on every magnet used to point at the bare BOOKING_URL (POD01-130).

A prospect clicked "Claim This Listing", booked a call, and arrived carrying
nothing: not which business they were, not which of the three offers they had
been shown, not which page they had just read. Whoever picked up the call
rebuilt all of it live, and nothing measured which offer converts.

build_booking_url is the one place that assembles the link, so the three offer
types cannot drift apart. These tests pin the params and, just as importantly,
pin the DESTINATION: /letschat is deliberate (it replaced /advertise/, which
302s to a price list instead of a calendar) and adding attribution must not
quietly move it.
"""

import config
import generator
from test_generate_offer_lead_magnet_page import _intel, _mock_client, _prompt_text


def test_destination_is_unchanged_by_attribution():
    """The params go on the end. They do not move the booking page.

    /advertise/ 302s to the funnel homepage, a shop front rather than a booking
    form, which is why the link was moved to /letschat in the first place.
    """
    url = config.build_booking_url("get_listed", "poppieco-com")
    assert url.startswith("https://theresandiego.com/letschat?")


def test_carries_the_lead_the_offer_and_the_source():
    url = config.build_booking_url("sponsored_story", "poppieco-com")
    for param in (
        "utm_source=lead_magnet",
        "utm_medium=sponsored_story",
        "utm_campaign=tsd_outreach",
        "offer=sponsored_story",
        "lead_id=poppieco-com",
    ):
        assert param in url, param


def test_offer_key_distinguishes_the_three_magnets():
    """Which offer converts best is the question this exists to answer, so the
    three cannot share a value."""
    mediums = {
        config.build_booking_url(offer, "x").split("utm_medium=")[1].split("&")[0]
        for offer in ("get_listed", "sponsored_story", "smart_site")
    }
    assert len(mediums) == 3


def test_a_missing_prospect_id_omits_the_param_rather_than_sending_an_empty_one():
    """run_engine.py and the tests build pages without a prospect id. An empty
    lead_id= is worse than none: it looks like an attributed click that lost its
    lead, and something downstream will eventually filter on it."""
    url = config.build_booking_url("get_listed")
    assert "lead_id" not in url
    assert url.endswith("offer=get_listed")


def _prompt_for(monkeypatch, offer, prospect_id=""):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))
    generator.generate_offer_lead_magnet_page(offer, _intel(), prospect_id=prospect_id)
    return _prompt_text(captured)


def test_both_magnet_prompts_carry_the_attributed_link(monkeypatch):
    for offer in ("get_listed", "sponsored_story"):
        prompt = _prompt_for(monkeypatch, offer, "poppieco-com")
        assert f"utm_medium={offer}" in prompt
        assert "lead_id=poppieco-com" in prompt


def test_the_magnet_prompt_never_offers_the_bare_url_as_an_alternative(monkeypatch):
    """The prompt names the booking page in several places. If any one of them
    still showed the bare URL, the model could satisfy the instruction with an
    unattributed link and be technically right."""
    prompt = _prompt_for(monkeypatch, "sponsored_story", "poppieco-com")
    bare = "https://theresandiego.com/letschat"
    # Every mention should be the attributed form: same count of "?" suffixed
    # occurrences as of the URL itself.
    assert prompt.count(bare) == prompt.count(bare + "?")


def test_prospect_id_is_threaded_from_the_deploy_path(monkeypatch, tmp_path):
    """build_offer_page_site is what api.py actually calls. If the id stops here
    the params ship empty on every real build while the unit tests stay green."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))

    generator.build_offer_page_site("get_listed", _intel(), "poppieco-com")

    assert "lead_id=poppieco-com" in _prompt_text(captured)


def test_the_smart_site_claim_bar_is_attributed_too(monkeypatch):
    """Smart Site is the third offer type and shares the same booking page. If
    only the two new magnets carry params, every Free Website click still lands
    anonymous and the conversion comparison the params exist for is missing a
    third of its data."""
    import test_generate_page as tgp

    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: tgp._mock_client(captured))

    generator.generate_page(
        tgp._rich_intel(),
        generator._get_design_personality("other"),
        tgp._nav_plan()[0],
        tgp._nav_plan(),
        prospect_id="acmedental-com",
    )

    prompt = _prompt_text(captured)
    assert "utm_medium=smart_site" in prompt
    assert "lead_id=acmedental-com" in prompt


def test_the_booking_link_still_does_not_count_as_a_backlink():
    """_count_own_domain_links compares canonical hosts, and canonical_domain
    strips the query. Attribution params must not accidentally make our own CTA
    satisfy the prospect-backlink requirement."""
    url = config.build_booking_url("get_listed", "mayamooncollective-com")
    html = f'<a href="{url}">Claim This Listing</a>'
    assert generator._count_own_domain_links(html, "mayamooncollective.com") == 0
    assert generator._count_own_domain_links(html, "theresandiego.com") == 1
