"""Social profiles: read off the prospect's own page, and searched for when it
links none. The posts the Sponsored Story shows from them are pinned in
test_story_posts.py.

Nothing gathered socials before. The only source was api.py merging Apify's
three columns, so a lead Apify had nothing for shipped a Sponsored Story with
an empty Social row and no card, while its Instagram handle sat in the footer
of the page we had just scraped.

The risk this creates is publishing the WRONG account under a real business's
name on a page emailed to that business. These pin the two defences: a URL has
to be a profile rather than a share button or a post, and a searched handle has
to look like the business before it is believed.
"""

import pytest

import api
import intel


# ── what counts as a profile ─────────────────────────────────────────────────

def test_profiles_are_read_off_the_page():
    html = """
      <a href="https://www.instagram.com/aussiejosh.nbhd/">Instagram</a>
      <a href="https://www.facebook.com/lobsterlabus">Facebook</a>
      <a href="https://linkedin.com/company/tech-emulsion/">LinkedIn</a>
      <a href="https://www.tiktok.com/@lobsterlab.us">TikTok</a>
      <a href="https://www.youtube.com/@aussiejosh">YouTube</a>
    """
    assert intel._profile_links(html) == {
        "instagram_url": "https://www.instagram.com/aussiejosh.nbhd",
        "facebook_url": "https://www.facebook.com/lobsterlabus",
        "linkedin_url": "https://linkedin.com/company/tech-emulsion",
        "tiktok_url": "https://www.tiktok.com/@lobsterlab.us",
        "youtube_url": "https://www.youtube.com/@aussiejosh",
    }


@pytest.mark.parametrize("href", [
    # The share buttons. facebook.com/sharer/sharer.php?u=<their page> is on a
    # large share of sites and matches a naive facebook.com regex, so without
    # this the fact panel would publish a share endpoint as the business's page.
    "https://facebook.com/sharer/sharer.php?u=https://lobsterlab.us",
    "https://twitter.com/intent/tweet?url=x",
    "https://www.linkedin.com/shareArticle?url=x",
    # A post is not a profile. The live article embeds instagram.com/p/<id>,
    # so this exact shape is in the markup we parse.
    "https://www.instagram.com/p/DZp58uHzO8m/",
    "https://www.instagram.com/reel/DZp58uHzO8m/",
    "https://www.instagram.com/explore/tags/sandiego/",
    "https://www.facebook.com/profile.php?id=100064",
])
def test_share_buttons_and_posts_are_not_profiles(href):
    assert intel._profile_links(f'<a href="{href}">x</a>') == {}


def test_the_first_link_wins_because_it_is_the_header_or_footer_row():
    """Later matches are the share buttons under the article, not the account."""
    html = ('<a href="https://www.instagram.com/lobsterlab.us/">theirs</a>'
            '<a href="https://www.instagram.com/somewebdesigner/">the agency</a>')

    assert intel._profile_links(html)["instagram_url"] == "https://www.instagram.com/lobsterlab.us"


# ── the search fallback ──────────────────────────────────────────────────────

def test_no_search_when_their_own_page_already_links_instagram(monkeypatch):
    """The fallback costs a Firecrawl call. It only runs when the free read
    found nothing."""
    monkeypatch.setattr(intel, "search_instagram",
                        lambda *a, **k: pytest.fail("searched anyway"))
    html = '<a href="https://www.instagram.com/lobsterlab.us/">ig</a>'

    assert intel.find_socials(html, "Lobster Lab")["instagram_url"].endswith("lobsterlab.us")


def test_the_search_runs_when_the_page_links_none(monkeypatch):
    monkeypatch.setattr(intel, "search_instagram",
                        lambda *a, **k: "https://www.instagram.com/lobsterlab.us")

    found = intel.find_socials("<p>no socials here</p>", "Lobster Lab", "San Diego")

    assert found == {"instagram_url": "https://www.instagram.com/lobsterlab.us"}


def test_an_over_budget_build_skips_the_search_rather_than_the_page(monkeypatch):
    """scrape_site turns the fallback off once intel has eaten its budget. The
    free read off their own markup still happens."""
    monkeypatch.setattr(intel, "search_instagram",
                        lambda *a, **k: pytest.fail("searched while over budget"))
    html = '<a href="https://www.facebook.com/lobsterlabus">fb</a>'

    found = intel.find_socials(html, "Lobster Lab", search_fallback=False)

    assert found == {"facebook_url": "https://www.facebook.com/lobsterlabus"}


def test_a_searched_handle_that_is_not_the_business_is_thrown_away(monkeypatch):
    """The search returns whatever ranks. A profile card pointing at a
    competitor, on a page carrying this business's own name, is found by the one
    person certain to check it."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "test-key")

    class _Resp:
        status_code = 200
        @staticmethod
        def json():
            return {"data": {"web": [
                {"url": "https://www.instagram.com/someothercoffeeshop/"},
                {"url": "https://www.instagram.com/darkhorsecoffee/"},
            ]}}

    monkeypatch.setattr(intel.requests, "post", lambda *a, **k: _Resp())

    assert intel.search_instagram("Dark Horse Coffee Roasters") == \
        "https://www.instagram.com/darkhorsecoffee"


def test_no_firecrawl_key_means_no_search_rather_than_a_crash(monkeypatch):
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")

    assert intel.search_instagram("Lobster Lab") == ""


# ── the app's own socials still win ──────────────────────────────────────────

def test_the_apps_socials_merge_with_the_scrape_instead_of_replacing_them():
    """Apify's record is a verified Google Maps listing, so it wins on the keys
    it has. Assigning over the top threw away TikTok and YouTube, which Apify
    does not carry at all, whenever the app had a single Facebook url."""
    scraped = {"instagram_url": "https://www.instagram.com/from_the_footer",
               "tiktok_url": "https://www.tiktok.com/@theirs"}
    intel_dict = {"socials": dict(scraped)}

    api._merge_known(intel_dict, {"instagram_url": "https://www.instagram.com/from_apify"})

    assert intel_dict["socials"] == {
        "instagram_url": "https://www.instagram.com/from_apify",
        "tiktok_url": "https://www.tiktok.com/@theirs",
    }
