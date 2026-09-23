"""A Sponsored Story is sold as an article that RUNS on ThereSanDiego.com.

The mockup was a Tailwind landing page: centred headline, no site header, no
sidebar, no footer, fonts the model picked. Set beside the live page it was
plainly a different website, which is the one thing a "here is your story on
our site" preview cannot be.

The chrome is fixed markup in tsd_theme now and the model writes the article
only. These pin that split, and the three traps it creates: everything that
MEASURES the model's work has to read the article rather than the finished
page, because the chrome carries a link to the prospect's own site, There San
Diego's logo as the first image, and a disclosure paragraph long enough to win
the meta description.
"""

import re

import generator
import tsd_theme
from test_generate_offer_lead_magnet_page import _intel, _mock_client, _prompt_text

HERO = "https://cdn.example.com/hero.jpg"

FRAGMENT = f"""<h1>Dark Horse Coffee Roasters: A Normal Heights Institution</h1>
<div class="tsd-byline">By <strong>There San Diego Staff</strong> &middot; San Diego</div>
<img class="tsd-hero" src="{HERO}" alt="The roastery on Adams Avenue">
<p>Tucked into a quiet stretch of Adams Avenue, <a href="https://darkhorsecoffeeroasters.com">Dark
Horse Coffee Roasters</a> has been roasting small batches for this neighborhood since long before
the block filled up with places to sit down and drink one.</p>
<p>Their <a href="https://darkhorsecoffeeroasters.com">wholesale program</a> now supplies half a
dozen kitchens across the city.</p>
<p>Visit <a href="https://darkhorsecoffeeroasters.com">their site</a> to order a bag.</p>"""


def _build(monkeypatch, html=FRAGMENT, offer="sponsored_story", intel=None, assets=None):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured, html=html))
    page = generator.generate_offer_lead_magnet_page(
        offer, intel if intel is not None else _intel(),
        prospect_id="darkhorsecoffeeroasters-com---sponsored-story",
        public_base="https://www.gotheresandiego.com", photo_assets=assets,
    )
    return page, captured


# ── the chrome ───────────────────────────────────────────────────────────────

def test_the_page_is_the_there_san_diego_site_not_a_landing_page(monkeypatch):
    html, _ = _build(monkeypatch)

    assert tsd_theme.TSD_LOGO in html
    for label, url in tsd_theme.NAV:
        assert f'<a href="{url}">{label}</a>' in html
    assert "Sponsored Listing:" in html
    assert "<span>Business Details</span>" in html
    assert "<span>What's Hot</span>" in html
    assert "<span>Upcoming Events</span>" in html
    assert "There Media Group, LLC" in html


def test_the_sidebar_carries_real_tsd_posts_and_events_not_invented_ones(monkeypatch):
    """The same five posts and five events on every prospect's page, by design.
    A model asked to fill a local-events sidebar would invent local events."""
    html, _ = _build(monkeypatch)

    for _, url, image in tsd_theme.WHATS_HOT:
        assert url in html and image in html
    for event in tsd_theme.UPCOMING_EVENTS:
        _, url, image, day = event[:4]
        assert url in html and image in html and day in html


def test_the_article_lands_in_the_main_column(monkeypatch):
    html, _ = _build(monkeypatch)

    main = html[html.index('<div class="tsd-main">'):html.index('<aside class="tsd-side">')]
    assert "A Normal Heights Institution" in main
    assert "Tucked into a quiet stretch of Adams Avenue" in main


def test_the_offer_is_fixed_markup_so_the_model_cannot_reprice_it(monkeypatch):
    """The plans used to be a paragraph of prompt. One build rendered the
    monthly tiers as one-time fees."""
    html, captured = _build(monkeypatch)

    assert "$497<em>/month</em>" in html
    assert "guaranteed impressions" in html
    assert "70,000+" in html
    # And none of it is the model's to write any more.
    assert "$497" not in _prompt_text(captured)


def test_get_listed_gets_none_of_this(monkeypatch):
    """Scope. A directory profile is a different offer with its own prompt, and
    widening the chrome to it here would ship it untested."""
    html, _ = _build(monkeypatch, offer="get_listed",
                     html="<!DOCTYPE html><html><body><h1>Dark Horse</h1></body></html>")

    assert tsd_theme.TSD_LOGO not in html
    assert "Sponsored Listing:" not in html


# ── what the model is asked for ──────────────────────────────────────────────

def test_the_model_is_asked_for_a_fragment_and_told_not_to_style_it(monkeypatch):
    _, captured = _build(monkeypatch)
    prompt = _prompt_text(captured)

    assert "HTML FRAGMENT" in prompt
    assert "NO Tailwind" in prompt
    assert "no <!DOCTYPE>" in prompt
    # The chrome already carries every one of these.
    assert "Do not write a claim bar" in prompt


def test_a_model_that_writes_a_whole_document_anyway_is_unwrapped(monkeypatch):
    """Asking is not the same as knowing. A nested <html> inside the chrome's
    <body> renders, badly, and a Tailwind CDN tag restyles the whole page."""
    document = (
        "<!DOCTYPE html><html><head><title>Preview</title>"
        '<script src="https://cdn.tailwindcss.com"></script>'
        "<style>body{background:red}</style></head>"
        f"<body>{FRAGMENT}</body></html>"
    )
    html, _ = _build(monkeypatch, html=document)

    assert html.count("<!DOCTYPE html>") == 1
    assert html.count("<html") == 1
    assert html.count("<body") == 1
    assert "cdn.tailwindcss.com" not in html
    assert "background:red" not in html
    assert "Tucked into a quiet stretch of Adams Avenue" in html


# ── the social profile card ──────────────────────────────────────────────────

def test_the_finished_story_carries_the_profile_card(monkeypatch):
    """The live article embeds the business's Instagram, and clicking through
    opens their account. Built server-side, never by the model: the handle on
    the card and the href behind the button have to be the same account."""
    intel = _intel(socials={"instagram_url": "https://www.instagram.com/darkhorsesd"})
    html, _ = _build(monkeypatch, intel=intel)

    assert 'href="https://www.instagram.com/darkhorsesd"' in html
    assert ">darkhorsesd<" in html
    assert "View profile" in html


def test_a_lead_with_no_socials_gets_no_card(monkeypatch):
    html, _ = _build(monkeypatch, intel=_intel(socials={}))

    # The class name is still in the stylesheet; what must be absent is markup.
    assert '<div class="tsd-embed">' not in html
    assert "View profile" not in html


def test_the_card_is_not_mistaken_for_the_story_photo(monkeypatch):
    """The card is inserted after og:image is read. A social url landing in
    og:image would put an Instagram logo on every share of the story."""
    intel = _intel(socials={"instagram_url": "https://www.instagram.com/darkhorsesd"})
    html, _ = _build(monkeypatch, intel=intel)

    assert re.search(r'<meta property="og:image" content="([^"]*)">', html).group(1) == HERO


# ── the three traps the wrap creates ─────────────────────────────────────────

def test_the_sidebars_website_row_does_not_satisfy_the_backlink_guard(monkeypatch, capsys):
    """The Business Details panel links to the prospect's site because the live
    one does. Counted after the wrap, a story with two backlinks would pass a
    guard that exists because a story shipped with none."""
    two = FRAGMENT.replace(
        '<p>Visit <a href="https://darkhorsecoffeeroasters.com">their site</a> to order a bag.</p>',
        "<p>Visit them to order a bag.</p>",
    )
    html, _ = _build(monkeypatch, html=two)

    assert "https://darkhorsecoffeeroasters.com" in html  # the sidebar row is there
    assert "carries 2 link(s)" in capsys.readouterr().out


def test_og_image_is_the_story_photo_not_the_there_san_diego_logo(monkeypatch):
    """The chrome's first <img> is TSD's logo, and it sits above the article."""
    html, _ = _build(monkeypatch)

    assert re.search(r'<meta property="og:image" content="([^"]*)">', html).group(1) == HERO


def test_the_description_is_the_story_not_the_sponsorship_disclosure(monkeypatch):
    """The disclosure is the first long <p> in the finished document and says
    nothing about the business. It is exactly what a naive first-paragraph rule
    picks, and it is the line Google would print."""
    html, _ = _build(monkeypatch)
    description = re.search(r'<meta name="description" content="([^"]*)">', html).group(1)

    assert description.startswith("Tucked into a quiet stretch of Adams Avenue")
    assert "Sponsored Listing" not in description


# ── the fact panel ───────────────────────────────────────────────────────────

def test_the_fact_panel_omits_a_row_rather_than_printing_not_listed(monkeypatch):
    """The live panel never writes "Not listed". This is the one block a
    prospect reads as data rather than prose."""
    html, _ = _build(monkeypatch, intel=_intel(phone="", neighborhood="", location=""))

    panel = html[html.index("Business Details"):html.index("<span>What's Hot</span>")]
    assert "Not listed" not in panel
    assert "Phone" not in panel
    assert "Address" not in panel
    # What we do hold still ships.
    assert "https://darkhorsecoffeeroasters.com" in panel
