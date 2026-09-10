"""A Sponsored Story is sold as a real published article (POD01-126).

The live Maya Moon story had a real editorial <title> and nothing else: no
description, no Open Graph, no canonical, no structured data. It looked perfect
in a browser, because none of this shows in a browser. It showed up as a bare
link with no picture the moment There San Diego shared it, which is the one
channel the offer is sold on.

These pin the six head tags, and the two traps underneath them: og:image has to
survive photo inlining as a fetchable address rather than a data: URI, and a
canonical tag must be absent rather than wrong when nobody told us the host.
"""

import json
import re
from datetime import date

import generator
from test_generate_offer_lead_magnet_page import _intel, _mock_client

PUBLIC_BASE = "https://www.gotheresandiego.com"
SLUG = "darkhorsecoffeeroasters-com---sponsored-story"
HERO = "https://cdn.example.com/hero.jpg"

# Shaped like the real thing: a claim bar whose paragraph comes FIRST and says
# nothing about the business, then the headline, the hero photo, and the story.
ARTICLE = f"""<!DOCTYPE html>
<html>
<head><title>Dark Horse Coffee Roasters: A Normal Heights Institution | There San Diego</title></head>
<body>
  <div class="claim-bar">
    <p>This is a preview of your Sponsored Story</p>
    <a href="https://theresandiego.com/letschat">Claim This Feature</a>
  </div>
  <h1>Dark Horse Coffee Roasters: A Normal Heights Institution</h1>
  <img src="{HERO}" alt="The roastery on Adams Avenue">
  <p>Tucked into a quiet stretch of Adams Avenue, <a href="https://darkhorsecoffeeroasters.com">Dark
  Horse Coffee Roasters</a> has been roasting small batches for this neighborhood since long before
  the block filled up with places to sit down and drink one.</p>
  <p>Their <a href="https://darkhorsecoffeeroasters.com">wholesale program</a> now supplies half a
  dozen kitchens across the city.</p>
  <p>Visit <a href="https://darkhorsecoffeeroasters.com">their site</a> to order a bag.</p>
</body>
</html>"""


def _build(monkeypatch, offer="sponsored_story", html=ARTICLE,
           public_base=PUBLIC_BASE, assets=None, intel=None):
    captured = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client(captured, html=html))
    return generator.generate_offer_lead_magnet_page(
        offer, intel if intel is not None else _intel(), prospect_id=SLUG,
        public_base=public_base, photo_assets=assets,
    )


def _meta(html, attr, key):
    match = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">', html)
    return match.group(1) if match else None


def _json_ld(html):
    match = re.search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>', html, re.DOTALL
    )
    return json.loads(match.group(1)) if match else None


def test_the_six_required_head_tags_are_all_present(monkeypatch):
    """One of six was shipping. The ticket lists all six by name."""
    html = _build(monkeypatch)

    assert _meta(html, "name", "description")
    assert f'<link rel="canonical" href="{PUBLIC_BASE}/preview/{SLUG}">' in html
    assert _meta(html, "property", "og:title")
    assert _meta(html, "property", "og:type") == "article"
    assert _meta(html, "property", "og:url") == f"{PUBLIC_BASE}/preview/{SLUG}"
    assert _meta(html, "name", "twitter:card") == "summary_large_image"
    assert _json_ld(html) is not None


def test_get_listed_is_left_exactly_as_it_was(monkeypatch):
    """Scope. A directory profile is not a published article, and POD01-125 asks
    for none of this. Widening it here would ship untested schema on the other
    offer under cover of this ticket."""
    html = _build(monkeypatch, offer="get_listed")

    assert "og:title" not in html
    assert "application/ld+json" not in html
    assert 'rel="canonical"' not in html


def test_og_image_survives_photo_inlining_as_a_fetchable_address(monkeypatch):
    """The trap. Every photo on the live page is embedded as base64, so reading
    the hero AFTER inlining would put a data: URI in og:image, and no crawler
    can fetch bytes out of a page it has not been given. Facebook needs an
    address to GET."""
    data_uri = "data:image/webp;base64,UklGRhoAAABXRUJQ"
    html = _build(monkeypatch, assets={HERO: data_uri})

    assert _meta(html, "property", "og:image") == HERO
    assert _meta(html, "name", "twitter:image") == HERO
    # The visible <img> still gets inlined; only the tag keeps the address.
    assert f'src="{data_uri}"' in html


def test_no_image_means_no_image_tag_and_a_small_card(monkeypatch):
    html = _build(monkeypatch, html=ARTICLE.replace(f'<img src="{HERO}" alt="The roastery on Adams Avenue">', ""))

    assert _meta(html, "property", "og:image") is None
    assert _meta(html, "name", "twitter:card") == "summary"


def test_no_public_base_means_no_canonical_rather_than_a_wrong_one(monkeypatch):
    """A canonical naming the wrong host tells a crawler the real page is
    somewhere else, and the somewhere else is a 404. Silence beats a wrong
    answer, and run_engine.py and smoke runs have no app to ask."""
    html = _build(monkeypatch, public_base="")

    assert 'rel="canonical"' not in html
    assert _meta(html, "property", "og:url") is None
    # Everything that does not depend on the host still ships.
    assert _meta(html, "property", "og:title")
    assert _json_ld(html)["headline"].startswith("Dark Horse")


def test_a_trailing_slash_on_the_public_base_does_not_double_up(monkeypatch):
    html = _build(monkeypatch, public_base=PUBLIC_BASE + "/")

    assert _meta(html, "property", "og:url") == f"{PUBLIC_BASE}/preview/{SLUG}"


def test_the_description_is_the_story_not_the_claim_bar(monkeypatch):
    """The first <p> in the document is "This is a preview of your Sponsored
    Story", which describes the mockup and says nothing about the business. It
    is also exactly what a naive "first paragraph" rule would pick."""
    html = _build(monkeypatch)
    description = _meta(html, "name", "description")

    assert description.startswith("Tucked into a quiet stretch of Adams Avenue")
    assert "preview of your Sponsored Story" not in description


def test_a_long_description_is_cut_on_a_word_boundary(monkeypatch):
    html = _build(monkeypatch)
    description = _meta(html, "name", "description")

    assert len(description) <= 156, description
    assert description.endswith("…")
    assert not description[:-1].endswith(" ")


def test_the_description_falls_back_to_the_scrape_and_never_to_filler(monkeypatch):
    """Nothing is invented on a page sold as editorial. When the model wrote no
    real paragraph, the scraped description stands in; when there is neither,
    the tag is omitted."""
    stripped = re.sub(r"<p>.*?</p>", "", ARTICLE, flags=re.DOTALL)

    html = _build(monkeypatch, html=stripped)
    assert _meta(html, "name", "description") == "Small-batch coffee roaster in Normal Heights."

    blank = _build(monkeypatch, html=stripped, intel=_intel(description=""))
    assert _meta(blank, "name", "description") is None


def test_the_structured_data_describes_the_article_and_the_business(monkeypatch):
    data = _json_ld(_build(monkeypatch))

    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "Article"
    assert data["headline"] == "Dark Horse Coffee Roasters: A Normal Heights Institution"
    assert data["datePublished"] == date.today().isoformat()
    assert data["mainEntityOfPage"] == f"{PUBLIC_BASE}/preview/{SLUG}"
    assert data["author"]["name"] == "There San Diego"
    assert data["publisher"]["name"] == "There San Diego"

    business = data["about"]
    assert business["@type"] == "LocalBusiness"
    assert business["name"] == "Dark Horse Coffee Roasters"
    assert business["url"] == "https://darkhorsecoffeeroasters.com"
    assert business["telephone"] == "555-1234"


def test_the_structured_data_carries_no_empty_or_invented_fields(monkeypatch):
    """A schema key with a placeholder value is the structured-data version of a
    fabricated review: it reads as a fact and nobody wrote it."""
    intel = _intel(phone="", location="")
    data = _json_ld(_build(monkeypatch, intel=intel))

    assert "telephone" not in data["about"]
    assert "areaServed" not in data["about"]
    assert all(v not in ("", None, {}, []) for v in data.values())


def test_a_business_name_cannot_break_out_of_the_script_tag(monkeypatch):
    """The name is scraped text we do not control. json.dumps escapes quotes; it
    does not escape "</", and "</script>" inside a script element ends it no
    matter what the JSON says."""
    intel = _intel(business_name='Dark </script><script>alert(1)</script> Horse')
    html = _build(monkeypatch, intel=intel)

    block = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    ).group(1)
    assert "</script>" not in block
    assert json.loads(block)["about"]["name"] == 'Dark </script><script>alert(1)</script> Horse'


def test_quotes_in_real_copy_are_escaped_in_the_meta_tags(monkeypatch):
    stripped = re.sub(r"<p>.*?</p>", "", ARTICLE, flags=re.DOTALL)
    intel = _intel(description='They call it "the best cup in Normal Heights" and mean it.')

    html = _build(monkeypatch, html=stripped, intel=intel)

    assert "&quot;the best cup in Normal Heights&quot;" in html
    assert _meta(html, "name", "description") is not None


def test_the_tags_go_inside_the_head_and_leave_the_model_title_alone(monkeypatch):
    html = _build(monkeypatch)

    assert html.count("<head>") == 1
    assert html.count("</head>") == 1
    assert "<title>Dark Horse Coffee Roasters: A Normal Heights Institution" in html
    head = html[html.index("<head>"):html.index("</head>")]
    assert 'rel="canonical"' in head
    assert "og:title" in head
    assert "application/ld+json" in head


def test_a_page_the_model_wrote_without_a_head_still_gets_one(monkeypatch):
    """_close_truncated_html guarantees a closing </body></html>, never a head.
    A page that lost its head to truncation still deserves its canonical."""
    headless = ARTICLE.replace(
        "<head><title>Dark Horse Coffee Roasters: A Normal Heights Institution | There San Diego</title></head>",
        "",
    )
    html = _build(monkeypatch, html=headless)

    assert html.count("<head>") == 1
    assert 'rel="canonical"' in html
    assert html.index("<head>") < html.index("<body>")
