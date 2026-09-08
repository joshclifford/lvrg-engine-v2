"""The `offer` string is what decides whether a build is a SITE or a single
lead-magnet mockup. Nothing called generate_offer_lead_magnet_page before this
routing existed, so these guard the wiring rather than the generator."""

import os
from unittest.mock import patch

import api
import generator


def _intel():
    return {
        "business_name": "insideOUT",
        "business_type": "restaurant",
        "domain": "insideoutsd.com",
        "description": "A North Park restaurant.",
        "services": ["Brunch", "Dinner"],
        "location": "San Diego, CA",
        "neighborhood": "North Park",
        "photos": [],
        "reviews": [],
        "press_mentions": [],
        "socials": {},
    }


def test_the_two_magnet_offers_map_to_generator_keys():
    assert api.OFFER_PAGE_OFFERS == {
        "Get Listed": "get_listed",
        "Sponsored Story": "sponsored_story",
    }


def test_site_offers_are_not_in_the_map_so_they_keep_the_old_path():
    """A regression here would silently turn every Smart Site into a one-page
    mockup, which is the worst possible failure mode for this change."""
    for offer in ("Smart Site", "Website Rebuild", "Website Grade", "AI Chat"):
        assert offer not in api.OFFER_PAGE_OFFERS


def test_cli_accepts_both_magnet_offers():
    import run_engine

    parser_src = open("run_engine.py", encoding="utf-8").read()
    assert '"Get Listed"' in parser_src
    assert '"Sponsored Story"' in parser_src


def test_build_offer_page_site_writes_index_html_and_returns_the_dir(tmp_path):
    with patch.object(generator, "SITES_DIR", str(tmp_path)), \
         patch.object(generator, "generate_offer_lead_magnet_page",
                      return_value="<html><body>page</body></html>") as gen, \
         patch.object(generator, "_build_chat_widget", return_value="<!--widget-->"):
        out = generator.build_offer_page_site("get_listed", _intel(), "acme-com",
                                              vertical="restaurant")

    assert out == os.path.join(str(tmp_path), "acme-com")
    written = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert "page" in written
    # The prompt says the widget is injected separately, so the wrapper owes it.
    assert "<!--widget-->" in written
    # And it must land INSIDE the body, not after </html>.
    assert written.index("<!--widget-->") < written.index("</body>")
    gen.assert_called_once()
    assert gen.call_args.kwargs["vertical"] == "restaurant"


def test_vertical_is_forwarded_as_none_when_blank(tmp_path):
    """An unknown business_type upstream sends "", and the generator's own
    fallback expects None, not an empty string."""
    with patch.object(generator, "SITES_DIR", str(tmp_path)), \
         patch.object(generator, "generate_offer_lead_magnet_page",
                      return_value="<html><body>x</body></html>") as gen, \
         patch.object(generator, "_build_chat_widget", return_value=""):
        generator.build_offer_page_site("sponsored_story", _intel(), "acme-com",
                                        vertical=None)
    assert gen.call_args.kwargs["vertical"] is None
