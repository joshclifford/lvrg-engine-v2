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


def test_offer_magnets_get_the_standalone_page_budget_not_the_fragment_one(monkeypatch):
    """These are standalone full pages at 10-11 sections. On PAGE_MAX_TOKENS the
    overflow truncates the tail, which is the pricing table and the CTA, and
    _close_truncated_html then repairs it into a valid page with nothing to click."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    assert captured[0]["max_tokens"] == generator.OFFER_PAGE_MAX_TOKENS
    assert captured[0]["max_tokens"] > generator.PAGE_MAX_TOKENS


def test_number_ranges_become_hyphens_not_commas():
    assert generator._strip_em_dashes("Open 9—5 daily") == "Open 9-5 daily"
    assert generator._strip_em_dashes("10–20 miles") == "10-20 miles"


@pytest.mark.parametrize(
    "text",
    [
        "We serve tacos, burritos, etc., and more.",
        "Open Mon, Fri. Closed Sun.",
        "<style>font-family: Arial, sans-serif;</style>",
        "Hours: 9-5, Mon to Fri",
        "Rated 4.8 stars, 312 reviews",
    ],
)
def test_text_without_a_dash_is_returned_untouched(text):
    """The first version rewrote punctuation globally after the swap, so
    "etc., and" came back as "etc. and" with no dash anywhere in the input."""
    assert generator._strip_em_dashes(text) == text


@pytest.mark.parametrize(
    "char", ["‒", "–", "—", "―"]
)
def test_every_dash_character_is_covered_not_just_em_and_en(char):
    assert char not in generator._strip_em_dashes(f"left {char} right")


@pytest.mark.parametrize(
    "entity", ["&mdash;", "&ndash;", "&#8212;", "&#8211;", "&#x2014;", "&#X2014;", "&#8212"]
)
def test_entity_spellings_including_uppercase_hex_and_missing_semicolon(entity):
    out = generator._strip_em_dashes(f"left {entity} right")
    assert "&" not in out
    assert not any(c in out for c in "‒–—―")


def test_dash_opening_a_text_node_does_not_leave_a_leading_comma():
    assert generator._strip_em_dashes("<p>— Hello</p>") == "<p>Hello</p>"


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<p>text —</p>", "<p>text</p>"),
        ("<h1>Kickserv —</h1>", "<h1>Kickserv</h1>"),
        ("trailing —", "trailing"),
    ],
)
def test_dash_closing_a_text_node_leaves_no_dangling_comma(html, expected):
    """"<p>text —</p>" rendered as "text," and read as a typo on the preview."""
    assert generator._strip_em_dashes(html) == expected


def test_dash_before_an_opening_inline_tag_keeps_its_comma():
    """The dangling-comma fix must not eat a real separator. Only a CLOSING
    tag means the dash was trailing."""
    assert generator._strip_em_dashes("<p>a — <em>b</em></p>") == "<p>a, <em>b</em></p>"


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://x.com/a—b">go</a>',
        '<a href="/x/9—5">t</a>',
        "<img src='/p—1.jpg'>",
    ],
)
def test_href_and_src_values_are_never_rewritten(html):
    """A comma and a space inside a URL breaks the link. Unreachable today,
    since these pages carry only BOOKING_URL, but not something to leave armed."""
    assert generator._strip_em_dashes(html) == html


@pytest.mark.parametrize(
    "html,expected",
    [
        (
            "<p><strong>Kickserv</strong> — field service software</p>",
            "<p><strong>Kickserv</strong>, field service software</p>",
        ),
        (
            "<li><b>Local</b> — 10,000 impressions</li>",
            "<li><b>Local</b>, 10,000 impressions</li>",
        ),
        ("<p><em>Colepepper</em> — 24/7 plumbing</p>", "<p><em>Colepepper</em>, 24/7 plumbing</p>"),
    ],
)
def test_dash_after_a_closing_inline_tag_keeps_its_separator(html, expected):
    """Treating every ">" as "the dash opened a text node" deleted the dash and
    its whitespace after </strong>, fusing the words: "Kickservfield service".
    The label-then-dash shape is exactly what the PLANS and services sections
    produce, so this rendered as a broken page on the offers this PR added."""
    assert generator._strip_em_dashes(html) == expected


def test_dash_after_an_opening_tag_still_drops(monkeypatch):
    """The counterpart the fix must not break: here the dash really did open
    the text node, so no comma belongs."""
    assert generator._strip_em_dashes("<p>— Hello</p>") == "<p>Hello</p>"


@pytest.mark.parametrize(
    "html",
    [
        '<img srcset="/a—1.jpg 1x">',
        "<a href=/a—b>x</a>",
        "<img data-src='/a—b.jpg'>",
    ],
)
def test_srcset_and_unquoted_urls_are_protected_too(html):
    assert generator._strip_em_dashes(html) == html


@pytest.mark.parametrize(
    "html",
    [
        "the &ndashboard is here",
        '<a href="/p?x=1&mdashboard=1">x</a>',
    ],
)
def test_entity_match_does_not_eat_the_prefix_of_a_longer_word(html):
    """An optional semicolon also matched the "&mdash" inside "&mdashboard=1",
    leaving "—board=1". The name must not run straight into more letters."""
    assert generator._strip_em_dashes(html) == html


def test_entities_inside_a_protected_url_are_left_alone():
    """The entity pass ran over the whole document before the split, so it
    wrote inside the very attributes the split exists to protect."""
    html = '<a href="/a&mdash;b">x</a>'
    assert generator._strip_em_dashes(html) == html


def test_trailing_dash_inside_an_attribute_value_leaves_no_comma():
    """Inside a tag the run ends at the value's closing quote, not at a
    closing tag, so title="ends —" must not become title="ends, "."""
    assert (
        generator._strip_em_dashes('<a title="ends —" href="/y">x</a>')
        == '<a title="ends" href="/y">x</a>'
    )
    # ... while a mid-value dash still becomes a comma.
    assert generator._strip_em_dashes('<a title="a — b">x</a>') == '<a title="a, b">x</a>'


def test_protection_is_scoped_to_the_url_not_the_whole_tag():
    assert (
        generator._strip_em_dashes('<img src="/p—1.jpg" alt="a—b">')
        == '<img src="/p—1.jpg" alt="a, b">'
    )


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
