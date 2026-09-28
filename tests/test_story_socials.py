"""The business's socials on a Sponsored Story (POD01-238).

They were plain grey words in the sidebar ("Instagram Facebook Linkedin
Youtube") that nobody read as links, and the story itself had none at all.
Now: round icon links in the sidebar's Social row, and the same icons as a
"Follow" line at the end of the story. Fixed markup from intel, never the
model's, because a social link the model wrote could be somebody else's.
"""

import tsd_theme
from test_generate_offer_lead_magnet_page import _intel
from test_sponsored_story_chrome import FRAGMENT, _build

IG = "https://www.instagram.com/darkhorsesd"
FB = "https://www.facebook.com/darkhorsecoffee"
YT = "https://www.youtube.com/@darkhorsecoffee"
LI = "https://www.linkedin.com/company/dark-horse"

ALL = {"linkedin_url": LI, "youtube_url": YT, "facebook_url": FB, "instagram_url": IG}

FACTBOX = ('<div class="tsd-factbox"><h3>The Details</h3><ul>'
           '<li><strong>Neighborhood:</strong> Normal Heights</li>'
           '<li><strong>Phone:</strong> 555-1234</li></ul></div>')


def _sidebar(html):
    return html[html.index("<span>Business Details</span>"):html.index("<span>What's Hot</span>")]


def _article(html):
    return html[html.index('<article class="tsd-article">'):html.index("</article>")]


# ── the icons ────────────────────────────────────────────────────────────────

def test_each_social_is_an_icon_link_in_platform_order():
    row = tsd_theme.social_icons(_intel(socials=ALL))

    hrefs = [IG, FB, YT, LI]
    positions = [row.index(f'href="{u}"') for u in hrefs]
    assert positions == sorted(positions)
    assert row.count('class="tsd-social-icon"') == 4
    assert row.count("<svg") == 4
    assert 'target="_blank"' in row


def test_names_are_spelt_the_way_the_platforms_spell_them():
    row = tsd_theme.social_icons(_intel(socials={**ALL, "tiktok_url": "https://www.tiktok.com/@dh"}))

    for name in ("Instagram", "Facebook", "TikTok", "YouTube", "LinkedIn"):
        assert f'title="{name}"' in row
    assert "Youtube" not in row and "Linkedin" not in row and "Tiktok" not in row
    assert 'aria-label="Dark Horse Coffee Roasters on Instagram"' in row


def test_no_socials_means_no_icons():
    assert tsd_theme.social_icons(_intel(socials={})) == ""
    assert tsd_theme.social_icons(_intel(socials={"instagram_url": ""})) == ""
    assert tsd_theme.social_icons(_intel(socials=None)) == ""


def test_only_web_addresses_reach_an_href():
    row = tsd_theme.social_icons(_intel(socials={
        "instagram_url": "javascript:alert(1)",
        "facebook_url": "www.facebook.com/darkhorsecoffee",
        "youtube_url": "not a url",
    }))

    assert "javascript" not in row
    assert 'href="https://www.facebook.com/darkhorsecoffee"' in row
    assert "not a url" not in row
    assert row.count('class="tsd-social-icon"') == 1


def test_a_platform_we_cannot_draw_is_left_out():
    row = tsd_theme.social_icons(_intel(socials={"instagram_url": IG, "yelp_url": "https://yelp.com/biz/x"}))

    assert "yelp" not in row
    assert row.count('class="tsd-social-icon"') == 1


# ── the Follow line in the story ─────────────────────────────────────────────

def test_the_follow_line_is_the_last_row_of_the_details_box():
    article = "<p>One.</p>" + FACTBOX + "<p>After.</p>"
    out = tsd_theme.insert_follow_row(article, _intel(socials=ALL))

    box = out[out.index('<div class="tsd-factbox">'):out.index("<p>After.</p>")]
    assert '<li class="tsd-follow"><strong>Follow:</strong>' in box
    assert box.index("tsd-follow") > box.index("Phone:")
    assert box.rstrip().endswith("</li></ul></div>")
    assert "tsd-follow-strip" not in out


def test_a_details_box_without_a_list_still_gets_one():
    article = '<p>One.</p><div class="tsd-factbox"><h3>The Details</h3></div>'
    out = tsd_theme.insert_follow_row(article, _intel(socials={"instagram_url": IG}))

    assert '<ul><li class="tsd-follow">' in out
    assert out.endswith("</li></ul></div>")


def test_no_details_box_means_a_follow_strip_after_the_last_paragraph():
    article = "<p>One.</p>\n<p>Last.</p>"
    out = tsd_theme.insert_follow_row(article, _intel(socials={"instagram_url": IG}))

    assert out.index("tsd-follow-strip") > out.index("<p>Last.</p>")
    assert "Follow Dark Horse Coffee Roasters" in out


def test_no_socials_leaves_the_story_untouched():
    article = "<p>One.</p>" + FACTBOX
    assert tsd_theme.insert_follow_row(article, _intel(socials={})) == article


# ── the built page ───────────────────────────────────────────────────────────

def test_a_built_story_carries_icons_in_the_sidebar_and_a_follow_line(monkeypatch):
    html, _ = _build(monkeypatch, html=FRAGMENT + FACTBOX, intel=_intel(socials=ALL))

    sidebar = _sidebar(html)
    assert "SOCIAL" in sidebar.upper()
    assert sidebar.count('class="tsd-social-icon"') == 4
    assert ">Instagram</a>" not in sidebar  # the old plain-word links

    article = _article(html)
    assert '<li class="tsd-follow">' in article
    assert f'href="{IG}"' in article


def test_a_built_story_with_no_socials_has_no_social_row_or_follow_line(monkeypatch):
    html, _ = _build(monkeypatch, html=FRAGMENT + FACTBOX, intel=_intel(socials={}))

    # The class names are in the stylesheet; what must be absent is markup.
    assert 'class="tsd-social-icon"' not in html
    assert '<div class="tsd-dt">Social</div>' not in html
    assert 'class="tsd-follow' not in html


def test_social_links_do_not_count_as_backlinks(monkeypatch, capsys):
    """The guard counts links to the business's own site. A socials line must
    never be what lets a story with too few of them pass."""
    two = FRAGMENT.replace(
        '<p>Visit <a href="https://darkhorsecoffeeroasters.com">their site</a> to order a bag.</p>',
        "<p>Visit them to order a bag.</p>",
    )
    _build(monkeypatch, html=two + FACTBOX, intel=_intel(socials=ALL))

    assert "carries 2 link(s)" in capsys.readouterr().out


def test_get_listed_gets_no_follow_line(monkeypatch):
    html, _ = _build(
        monkeypatch, offer="get_listed", intel=_intel(socials=ALL),
        html="<!DOCTYPE html><html><head></head><body>" + FACTBOX + "</body></html>",
    )

    assert "tsd-follow" not in html
