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


def test_sponsored_story_asks_for_editorial_voice_and_ships_the_real_pricing(monkeypatch):
    """The voice is the model's job and stays a prompt assertion. The prices are
    not: they moved into tsd_theme.PLANS as fixed markup, so this reads the
    finished page, which is the thing the prospect is quoted from."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    html = generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    assert "editorial" in _prompt_text(captured)
    assert "guaranteed impressions" in html
    for price in ("$497", "$997", "$1,500"):
        assert f"{price}<em>/month</em>" in html, price


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

    Reads the rendered page for the Sponsored Story numbers. They are fixed
    markup in tsd_theme now rather than a paragraph of prompt, so the prompt no
    longer carries them and asserting on it would pass an empty page. The $297
    is still the model's to write, so it stays a prompt assertion.
    """
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    html = generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    for tier, price, impressions in (
        ("LOCAL", "$497", "10,000"),
        ("CITYWIDE", "$997", "25,000"),
        ("COUNTYWIDE", "$1,500", "50,000"),
    ):
        assert f"<h3>{tier}</h3>" in html
        # Monthly, and said so on the page. One build rendered these as
        # one-time fees while the prompt that asked for them said "per month".
        assert f"{price}<em>/month</em>" in html
        assert f"{impressions} guaranteed impressions a month" in html

    # Audience figures, verbatim from the client's own "real numbers to hold to"
    # in campaign-advertising.md, which is the list TSD's own reps work from.
    #
    # 80,000+, not 82,000. The template carried 82,000 and this test asserted it,
    # so the guardrail agreed with the copy and neither noticed (POD01-129). The
    # client's figure is "80,000+ social followers (40k Facebook + 42k
    # Instagram)": someone added the two components, published the sum as an
    # exact count, and dropped the "+". It is a small inflation of a number the
    # client's own guardrails end with the words "Don't inflate", on a page whose
    # whole job is to be trusted by the business it names.
    #
    # Assert the "+" too. It is the difference between a floor TSD publishes and
    # an exact count nobody measured.
    for stat in ("70,000+", "700,000+", "25,000+", "80,000+"):
        assert stat in html
    assert "82,000" not in html

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
    """intel["reviews"] is never populated, so a CUSTOMER quote could only be
    filled by inventing one.

    The Sponsored Story does now ask for a pull quote (PM review, 8 Sep 2026).
    That is deliberate and safe: it is the business's own copy set large, which
    is ordinary editorial. What stays banned is attribution to a REVIEWER, which
    is what turns a design flourish into fabricated evidence. This test pins the
    attribution rule rather than the words "pull quote".

    Widened after POD01-133. The rule used to name only a customer, and the live
    Pop Pie Co page published "Yelp reviewers have specifically called out ...",
    which named no customer and so passed every guard while being the same
    invention. The ban is on the attribution, so the prompt has to close the
    unnamed-group and named-platform shapes too."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(offer, _intel())

    prompt = _prompt_text(captured)
    assert "YOU WERE GIVEN NO REVIEW TEXT" in prompt
    # The shapes that got through: an unnamed group, and a named platform.
    assert "Yelp" in prompt and "TripAdvisor" in prompt
    assert '"reviewers"' in prompt
    # A paraphrase is the same claim with the punctuation removed.
    assert "No unquoted paraphrase either" in prompt
    assert "TESTIMONIALS" not in prompt


def test_unknown_offer_raises_instead_of_silently_defaulting(monkeypatch):
    with pytest.raises(ValueError):
        generator.generate_offer_lead_magnet_page("get_rich_quick", _intel())


def test_no_nav_or_multi_page_language_since_this_is_a_standalone_page(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    # The wording changed on 8 Sep 2026. "no nav to other pages" was a plausible
    # reason every backlink got dropped: read literally it forbids linking out
    # at all, and the live page shipped with zero links to the business. The
    # standalone rule still has to be stated, so this pins the INTENT: no nav
    # bar, no links to sibling pages of this mockup, outbound links unaffected.
    lowered = prompt.lower()
    assert "single page" in lowered
    assert "no nav bar" in lowered
    assert "other pages of this mockup" in lowered


# ── backlinks and layout (PM review, 8 Sep 2026) ─────────────────────────────
# The Sponsored Story shipped with no link to the prospect's own site at all,
# on an offer sold as "published on ThereSanDiego.com with links back to your
# website". The backlink IS the product; the page was demonstrating everything
# except the part being bought.

@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_both_offers_are_told_to_link_to_the_prospects_real_domain(monkeypatch, offer):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    intel = _intel()
    intel["domain"] = "mayamooncollective.com"
    generator.generate_offer_lead_magnet_page(offer, intel)

    prompt = _prompt_text(captured)
    assert "https://mayamooncollective.com" in prompt
    # The rule has to say the links matter, not just that they exist.
    assert "never example.com" in prompt
    assert 'never add rel="nofollow"' in prompt.lower()


def test_sponsored_story_asks_for_three_backlinks_in_named_positions(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "at least THREE" in prompt
    assert "FIRST paragraph" in prompt


def test_sponsored_story_is_longer_than_the_five_paragraphs_the_pm_saw(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "7-9 paragraphs" in prompt
    # Length alone is padding; the structure is what makes it readable.
    assert "SUBHEADINGS" in prompt
    assert "PULL QUOTE" in prompt


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_photos_are_spread_through_the_page_not_stacked_on_top(monkeypatch, offer):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(offer, _intel())

    prompt = _prompt_text(captured)
    assert "BETWEEN sections or paragraphs" in prompt


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_no_stock_photo_fallback_when_the_lead_has_none(monkeypatch, offer):
    """A mockup carries the prospect's own branding. An obvious placeholder
    reads worse than no image at all."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(offer, _intel())

    prompt = _prompt_text(captured)
    assert "do NOT substitute stock photography" in prompt


def test_pull_quote_is_still_never_attributed_to_a_customer(monkeypatch):
    """The Sponsored Story now asks for a pull quote, which is the exact shape
    the invented-testimonial guard exists to stop. It must be drawn from their
    own copy, not put in a customer's mouth."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "YOU WERE GIVEN NO REVIEW TEXT" in prompt
    assert "THEIR OWN words about themselves" in prompt

    # Item 3 says the pull quote is "never in quotation marks as if someone said
    # it". An earlier version of item 5 called it "the only quotation this page
    # may carry", which reads as permission to add the marks item 3 forbids, on
    # the one element most likely to turn into a fake testimonial. The two items
    # have to agree.
    assert "WITHOUT quotation" in prompt
    assert "the only quotation this page may carry" not in prompt


# ── the backlink guard ──────────────────────────────────────────────────────
# The first prompt that asked for backlinks was ignored outright: the live
# Sponsored Story shipped with six links, every one of them ours, and none to
# the business being sold. Asking is not knowing.

def test_own_domain_links_are_counted_from_hrefs_not_body_text():
    """The business name and domain appear in the copy on every one of these
    pages. A substring search would report a page rich in backlinks when it has
    none, which is precisely the failure this guard exists to catch."""
    html = (
        '<a href="https://mayamooncollective.com">Maya Moon</a>'
        '<a href="https://www.mayamooncollective.com/menu">the menu</a>'
        '<p>Visit mayamooncollective.com for hours</p>'
    )
    assert generator._count_own_domain_links(html, "mayamooncollective.com") == 2


def test_booking_and_social_links_do_not_count_as_backlinks():
    """Both were present on the failing page. Counting either would let a page
    satisfy the requirement while never linking to the business itself."""
    html = (
        '<a href="https://theresandiego.com/letschat">Claim This Feature</a>'
        '<a href="https://www.instagram.com/mayamooncollective">Instagram</a>'
    )
    assert generator._count_own_domain_links(html, "mayamooncollective.com") == 0


def test_www_and_paths_still_count_as_the_same_site():
    html = (
        '<a href="https://www.acme.com/">home</a>'
        '<a href="http://acme.com/menu?x=1">menu</a>'
    )
    assert generator._count_own_domain_links(html, "acme.com") == 2


def test_a_missing_domain_counts_nothing_rather_than_crashing():
    assert generator._count_own_domain_links('<a href="https://x.com">x</a>', "") == 0


@pytest.mark.parametrize(
    "offer,minimum", [("sponsored_story", 3), ("get_listed", 2)]
)
def test_prompt_states_the_minimum_and_names_the_real_domain(monkeypatch, offer, minimum):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    intel = _intel()
    intel["domain"] = "mayamooncollective.com"
    generator.generate_offer_lead_magnet_page(offer, intel)

    prompt = _prompt_text(captured)
    assert f"MINIMUM {minimum} links" in prompt
    assert "https://mayamooncollective.com" in prompt
    # The booking link must be described as OURS, so it cannot stand in.
    assert "This is OUR link, not theirs" in prompt


def test_single_page_rule_no_longer_reads_as_a_ban_on_outbound_links(monkeypatch):
    """"no nav to other pages" was plausibly why every backlink was dropped: a
    reasonable reading of it is "do not link out"."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("sponsored_story", _intel())

    prompt = _prompt_text(captured)
    assert "This does NOT mean avoid links" in prompt


def test_too_few_backlinks_warns_but_still_returns_the_page(monkeypatch, capsys):
    """A thin page is still a usable mockup. Failing the build would cost the
    user a generation over something a rebuild may well fix."""
    bare = '<!DOCTYPE html><html><body><p>No links at all.</p></body></html>'
    captured = []
    monkeypatch.setattr(
        generator, "_get_client", lambda **k: _mock_client(captured, html=bare)
    )

    intel = _intel()
    intel["domain"] = "mayamooncollective.com"
    html = generator.generate_offer_lead_magnet_page("sponsored_story", intel)

    assert "No links at all." in html
    warning = capsys.readouterr().out
    assert "carries 0 link(s)" in warning
    assert "expected at least 3" in warning


# ── link to THEIR page, not the root ────────────────────────────────────────
# Su Pan Bakery is stored as https://supanbakery.com/en/ and the backlink went
# to the bare root. Harmless there, but it is POD01-34 in link form: when a
# business lives inside a larger site the root belongs to the PARENT, and the
# "visit their website" link sends the prospect to the wrong company.

def test_backlink_uses_the_leads_own_page_when_it_has_a_path(monkeypatch):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    intel = _intel()
    intel["domain"] = "supanbakery.com"
    intel["page_url"] = "https://supanbakery.com/en/"
    generator.generate_offer_lead_magnet_page("get_listed", intel)

    prompt = _prompt_text(captured)
    assert "https://supanbakery.com/en/" in prompt
    assert "Use it VERBATIM, including any path" in prompt


def test_root_domain_leads_still_link_to_the_domain(monkeypatch):
    """No path means no change: the overwhelming majority of leads."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    intel = _intel()
    intel["domain"] = "acme.com"
    generator.generate_offer_lead_magnet_page("sponsored_story", intel)

    prompt = _prompt_text(captured)
    assert "https://acme.com" in prompt


def test_a_path_link_still_counts_toward_the_backlink_minimum():
    """The counter compares hosts, so a link carrying the lead's path must not
    be missed just because it is not the bare root."""
    html = (
        '<a href="https://supanbakery.com/en/">Su Pan Bakery</a>'
        '<a href="https://supanbakery.com/en/menu">the pan dulce</a>'
    )
    assert generator._count_own_domain_links(html, "supanbakery.com") == 2


@pytest.mark.parametrize("offer", ["get_listed", "sponsored_story"])
def test_prompt_forbids_reusing_a_photo(monkeypatch, offer):
    """Four images in seven places produced a 5 MB page the proxy refused."""
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(offer, _intel())

    prompt = _prompt_text(captured)
    assert "USE EACH PHOTO AT MOST ONCE" in prompt
    assert "use fewer images" in prompt


def test_an_oversized_page_warns_instead_of_failing_silently(monkeypatch, capsys):
    """A page over the proxy cap builds fine, stores `ready`, and serves blank.
    Nothing else in the chain notices, so the generator has to say it."""
    monkeypatch.setattr(generator, "_PREVIEW_PROXY_WARN_BYTES", 1000)
    big = "<!DOCTYPE html><html><body>" + ("<p>filler</p>" * 400) + "</body></html>"
    captured = []
    monkeypatch.setattr(
        generator, "_get_client", lambda **k: _mock_client(captured, html=big)
    )

    generator.generate_offer_lead_magnet_page("get_listed", _intel())

    out = capsys.readouterr().out
    assert "preview proxy rejects anything over 5 MB" in out


FOOD_LINKS_POINT = "Website, menu, reservations and socials all linked in one place."


@pytest.mark.parametrize("vertical", ["realtor", "contractor", "retail", None])
def test_get_listed_only_promises_menu_and_reservations_to_food(monkeypatch, vertical):
    # A contractor's live page read "Website, menu, reservations and socials".
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("get_listed", _intel(), vertical=vertical)

    prompt = _prompt_text(captured)
    assert FOOD_LINKS_POINT not in prompt
    assert "Your website and socials all linked in one place." in prompt


@pytest.mark.parametrize("vertical", ["restaurant", "cafe"])
def test_get_listed_food_keeps_tsd_menu_and_reservations_wording(monkeypatch, vertical):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page("get_listed", _intel(), vertical=vertical)

    assert FOOD_LINKS_POINT in _prompt_text(captured)


def test_get_listed_pins_one_star_rating_format(monkeypatch):
    # Two live pages drew the rating two ways: five stars for a 5.0, one for a 4.7.
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured))

    generator.generate_offer_lead_magnet_page(
        "get_listed", _intel(rating=4.7, review_count=14), vertical="contractor"
    )

    prompt = _prompt_text(captured)
    assert "★ 4.7 (14 reviews)" in prompt
    assert "Never a row of stars" in prompt
