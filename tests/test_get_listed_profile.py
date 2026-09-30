"""Get Listed is a ThereSanDiego Business Profile (POD01-251).

It was a Tailwind landing page with a sales pitch inside it ("Why List Here",
"Social Proof", a $297 CTA). The live profiles, Josh Taylor and Elements
Design & Build, are the TSD site chrome around a short founder-led profile:
a named headline, the opening, What They're Known For, Credentials & Details,
one industry section, In Their Own Words, photos, a closing CTA to their site.

The model writes the profile. Credentials & Details, the gallery and the $297
offer are fixed markup from real data, and the owner quote only survives when
it is on their website word for word.
"""

import generator
import tsd_theme
from test_generate_offer_lead_magnet_page import _intel, _mock_client, _prompt_text

SITE = "https://darkhorsecoffeeroasters.com"
PHOTOS = [f"https://darkhorsecoffeeroasters.com/p{i}.jpg" for i in range(1, 5)]

SITE_TEXT = ("Welcome to Dark Horse. We started roasting in a garage in 2009 because we "
             "could not find a cup we wanted to drink every morning. Come by the shop.")

PROFILE = f"""<h1>Dark Horse Coffee Roasters, Normal Heights Roaster: Small Batches Since 2009</h1>
<p><a href="{SITE}">Dark Horse Coffee Roasters</a> started in a garage in Normal Heights in 2009,
roasting the coffee its founders could not find anywhere else in the neighborhood.</p>
<p>It still roasts in small batches and supplies kitchens across the city.</p>
<figure><img src="{PHOTOS[0]}" alt="The roastery"></figure>
<h2>What They're Known For</h2>
<ul><li>Roasting in Normal Heights since 2009.</li><li>Wholesale coffee for local kitchens.</li></ul>
{generator.CREDENTIALS_MARKER}
<h2>What to Order on Your First Visit</h2>
<p>Start with the house espresso.</p>
<h2>In Their Own Words</h2>
<blockquote><p>We started roasting in a garage in 2009 because we could not find a cup we wanted
to drink every morning.</p></blockquote>
<h2>Plan Your Visit</h2>
<p>Order a bag at <a href="{SITE}">darkhorsecoffeeroasters.com</a>.</p>"""


def _build(monkeypatch, html=PROFILE, vertical="cafe", assets=None, **intel):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured, html=html))
    page = generator.generate_offer_lead_magnet_page(
        "get_listed", _intel(**{"raw_text": SITE_TEXT, **intel}), vertical=vertical,
        prospect_id="darkhorsecoffeeroasters-com---get-listed", photo_assets=assets,
    )
    return page, _prompt_text(captured)


def _article(html):
    return html[html.index('<article class="tsd-article">'):html.index("</article>")]


# ── the page ─────────────────────────────────────────────────────────────────

def test_it_is_the_there_san_diego_site_with_the_listing_claim_bar(monkeypatch):
    html, _ = _build(monkeypatch)

    assert tsd_theme.TSD_LOGO in html
    assert "Sponsored Listing:" in html
    assert "This is a preview of your ThereSanDiego.com listing" in html
    assert "Claim This Listing &rarr;" in html
    assert "cdn.tailwindcss.com" not in html


def test_the_sections_follow_the_live_profile_order(monkeypatch):
    article = _article(_build(monkeypatch)[0])

    order = ["<h1>", "What They're Known For", "Credentials &amp; Details",
             "What to Order on Your First Visit", "In Their Own Words", "Plan Your Visit"]
    positions = [article.index(s) for s in order]
    assert positions == sorted(positions)


def test_the_sales_pitch_is_out_of_the_profile(monkeypatch):
    """The live profiles never mention a price. The offer is below the profile,
    outside the article, like the story's plans."""
    html, _ = _build(monkeypatch)
    article = _article(html)

    for pitch in ("$297", "Why List Here", "Social Proof", "Claim"):
        assert pitch not in article, pitch
    assert "<h3>TIER 1</h3>" in html and "$297" in html


def test_it_stays_different_from_a_sponsored_story(monkeypatch):
    html, prompt = _build(monkeypatch)

    assert "/month</em>" not in html
    assert 'class="tsd-post' not in html
    assert 'class="tsd-byline"' not in html
    assert "PULL QUOTE" not in prompt


def test_contractors_are_filed_under_the_contractors_directory(monkeypatch):
    html, _ = _build(monkeypatch, vertical="contractor")
    assert '<a href="https://theresandiego.com/san-diego-contractors/">San Diego Contractors</a>' in html


# ── Credentials & Details ────────────────────────────────────────────────────

def test_credentials_are_built_from_intel_where_the_model_marked(monkeypatch):
    html, _ = _build(monkeypatch, email="hello@darkhorse.com",
                     socials={"instagram_url": "https://www.instagram.com/darkhorsesd/"})
    article = _article(html)

    assert generator.CREDENTIALS_MARKER not in article
    block = article[article.index("Credentials &amp; Details"):article.index("What to Order")]
    for row in ("Company: Dark Horse Coffee Roasters", "Specialties: Coffee subscriptions, Wholesale",
                "Phone: 555-1234", "Hours: Mon-Sun 7-5", "Neighborhood: Normal Heights",
                'href="mailto:hello@darkhorse.com"', f'href="{SITE}"', ">@darkhorsesd</a>"):
        assert row in block, row


def test_a_missing_value_is_a_missing_row_not_not_listed():
    block = tsd_theme.credentials_list(_intel(phone="", hours="", services=[]), SITE)

    assert "Not listed" not in block
    assert "Phone" not in block and "Hours" not in block and "Specialties" not in block


def test_only_a_street_address_gets_a_map_link():
    assert "Google Maps" not in tsd_theme.credentials_list(_intel(location="San Diego, CA"), SITE)
    street = tsd_theme.credentials_list(_intel(location="3034 Canon St, San Diego, CA 92106"), SITE)
    assert ('href="https://www.google.com/maps/search/?api=1&amp;query='
            '3034+Canon+St%2C+San+Diego%2C+CA+92106"') in street


def test_a_suite_number_stays_in_the_map_search():
    """Felicia Lewis Group, 30 Sep: "#205" cut the link at the "#", Google
    searched "5965 Village Way" and showed the UPS Store next door."""
    block = tsd_theme.credentials_list(
        _intel(location="5965 Village Way #205, San Diego, CA 92130"), SITE)
    href = block.split('href="', 2)[1].split('"')[0]
    assert "#" not in href
    assert "%23205" in href and "92130" in href


def test_without_the_marker_credentials_go_before_the_second_heading():
    article = "<h1>X</h1><h2>Known For</h2><ul><li>a</li></ul><h2>Next</h2><p>b</p>"
    out = generator._place_credentials(article, "<h2>Credentials</h2>")
    assert out.index("Known For") < out.index("Credentials") < out.index("Next")


def test_the_credentials_website_row_does_not_pass_the_backlink_guard(monkeypatch, capsys):
    """The guard measures what the model wrote. One link from the model plus
    the fixed Website row must still warn."""
    one = PROFILE.replace(f'<a href="{SITE}">darkhorsecoffeeroasters.com</a>', "their site")
    _build(monkeypatch, html=one)
    assert "carries 1 link(s)" in capsys.readouterr().out


# ── In Their Own Words ───────────────────────────────────────────────────────

def test_a_quote_copied_from_their_site_is_kept(monkeypatch):
    article = _article(_build(monkeypatch)[0])
    assert "In Their Own Words" in article
    assert "<blockquote>" in article


def test_a_quote_that_is_not_on_their_site_is_dropped(monkeypatch, capsys):
    made_up = PROFILE.replace("could not find a cup we wanted\nto drink every morning",
                              "pour our hearts into every single cup we serve")
    article = _article(_build(monkeypatch, html=made_up)[0])

    assert "In Their Own Words" not in article
    assert "<blockquote>" not in article
    assert "Plan Your Visit" in article
    assert "not on their website" in capsys.readouterr().out


def test_no_site_text_means_no_quote_survives(monkeypatch):
    article = _article(_build(monkeypatch, raw_text="")[0])
    assert "In Their Own Words" not in article


def test_a_few_matching_words_are_not_a_quote():
    article = "<h2>In Their Own Words</h2><blockquote><p>Come by the shop.</p></blockquote>"
    assert "blockquote" not in generator._keep_only_real_quotes(article, {"raw_text": SITE_TEXT})


# ── photos ───────────────────────────────────────────────────────────────────

def test_unused_downloaded_photos_become_a_gallery_above_the_cta(monkeypatch):
    assets = {u: f"data:image/jpeg;base64,{i}" for i, u in enumerate(PHOTOS)}
    html, _ = _build(monkeypatch, photos=PHOTOS, assets=assets)
    article = _article(html)

    gallery = article[article.index('<div class="tsd-gallery">'):]
    assert gallery.count("<figure>") == 3
    assert article.index('<div class="tsd-gallery">') < article.index("Plan Your Visit")
    # The featured photo is not repeated in the gallery.
    assert article.count("data:image/jpeg;base64,0") == 1


def test_no_downloaded_photos_means_no_gallery(monkeypatch):
    html, _ = _build(monkeypatch, photos=PHOTOS)
    assert '<div class="tsd-gallery">' not in _article(html)


# ── what the model is asked for ──────────────────────────────────────────────

def test_the_prompt_asks_for_the_profile_fragment_in_order(monkeypatch):
    _, prompt = _build(monkeypatch)

    assert "HTML FRAGMENT" in prompt
    assert "What They're Known For" in prompt
    assert generator.CREDENTIALS_MARKER in prompt
    assert "In Their Own Words" in prompt
    assert "EXACTLY as written there" in prompt
    assert "No byline" in prompt


def test_the_prompt_carries_their_proof_and_site_text(monkeypatch):
    _, prompt = _build(monkeypatch, social_proof="Roasting since 2009. Good Food Award 2019.")

    assert "Good Food Award 2019" in prompt
    assert SITE_TEXT in prompt


def test_the_industry_section_changes_with_the_vertical(monkeypatch):
    assert "<h2>Personal Connection</h2>" in _build(monkeypatch, vertical="realtor")[1]
    contractor = _build(monkeypatch, vertical="contractor")[1]
    assert "One Question Worth Asking Any Contractor Before You Hire" in contractor
    assert "Start Your Project" in contractor
    assert "What to Order on Your First Visit" in _build(monkeypatch, vertical="restaurant")[1]
    assert "What to Know Before You Reach Out" in _build(monkeypatch, vertical=None)[1]
