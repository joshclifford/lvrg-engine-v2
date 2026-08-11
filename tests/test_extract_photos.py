"""extract_photos — real photos off the prospect's own page.

The bug this file exists for: the junk/promo filters were applied to the full
absolute URL, which carries the HOST. _JUNK_IMAGE contains substrings that occur
in ordinary business names ("badge", "blank", "arrow", "icon"), so a prospect at
blankslatecoffee.com lost every candidate to their own domain name, returned [],
and fell back to gradients with nothing logged — the same silent failure as the
Yelp 403 this code replaced.
"""

from intel import extract_photos, _photo_score

HTML = """
<meta property="og:image" content="/img/storefront.jpg">
<img src="/img/logo.png">
<img src="/img/giveaway-june-2026.jpg">
<img src="/img/team-photo.jpg">
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<img src="/img/diagram.svg">
"""

# Real business domains that collide with _JUNK_IMAGE substrings.
TRAP_HOSTS = [
    "blankslatecoffee.com",   # blank
    "badgerroofing.com",      # badge
    "arrowplumbing.com",      # arrow
    "iconicdental.com",       # icon
    "patternbrewing.com",     # pattern
]


def test_own_domain_is_not_treated_as_junk():
    for host in TRAP_HOSTS:
        photos = extract_photos(HTML, f"https://{host}")
        assert photos, f"{host} lost every photo to its own domain name"


def test_real_junk_is_still_dropped():
    photos = extract_photos(HTML, "https://blankslatecoffee.com")
    assert not any("logo.png" in p for p in photos)


def test_promo_art_ranks_below_a_real_photo():
    photos = extract_photos(HTML, "https://example.com")
    assert "giveaway" not in photos[0]


def test_data_uris_and_svg_are_skipped():
    photos = extract_photos(HTML, "https://example.com")
    assert not any(p.startswith("data:") for p in photos)
    assert not any(p.endswith(".svg") for p in photos)


def test_relative_sources_are_made_absolute():
    photos = extract_photos(HTML, "https://example.com")
    assert all(p.startswith("https://example.com/") for p in photos)


def test_promo_penalty_is_path_scoped():
    """A host containing 'promo' must not penalise every photo on the site."""
    assert _photo_score("https://promoplumbing.com/img/storefront.jpg") > 0


def test_junk_in_the_filename_still_scores_as_junk():
    """The fix narrows scope to the path — it must not disarm the filter."""
    photos = extract_photos(
        '<img src="/img/giveaway-banner.jpg"><img src="/img/kitchen.jpg">',
        "https://example.com",
    )
    assert photos[0].endswith("kitchen.jpg")


def test_no_html_returns_empty():
    assert extract_photos("", "https://example.com") == []


def test_query_string_extension_is_not_dropped():
    r"""Regression on the FIX, not the original bug.

    An earlier version of this fix narrowed the extension check to the URL path
    as well as the junk check. That silently dropped CDN URLs carrying the
    extension in the query (`/img?file=photo.jpg`) — re-creating the very
    zero-photo failure the fix exists to remove, just on different sites.
    _PHOTO_EXT allows `(\?|$)` on purpose; only the JUNK check is path-scoped.
    """
    photos = extract_photos(
        '<img src="/img?file=photo.jpg">', "https://example.com"
    )
    assert photos, "an image whose extension is in the query string was dropped"


def test_junk_is_still_path_scoped_when_the_extension_is_in_the_query():
    photos = extract_photos(
        '<img src="/logo?file=brand.png">', "https://example.com"
    )
    assert photos == []
