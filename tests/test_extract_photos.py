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


def test_junk_is_still_scoped_out_when_the_extension_is_in_the_query():
    photos = extract_photos(
        '<img src="/logo?file=brand.png">', "https://example.com"
    )
    assert photos == []


def test_junk_in_the_QUERY_is_dropped():
    """Regression on the fix, second direction.

    The first version of this fix scoped the junk check to `urlparse(u).path`,
    which drops the query as well as the host. CDNs that serve
    `/cdn?file=logo.png` then sailed straight through — and because the dedup
    key is `absolute.split("?")[0]`, every asset on such a site collapses to one
    entry, so the surviving logo became the hero of the prospect's own rebuilt
    page. Measured before the fix: this returned the logo where the pre-PR code
    had returned the storefront.

    `_sans_host` removes the host and nothing else, which is the only part that
    ever caused a false positive.
    """
    photos = extract_photos(
        '<img src="/cdn?file=logo.png"><img src="/cdn?file=storefront.jpg">',
        "https://acmeroofing.com",
    )
    assert photos, "the real photo was lost"
    assert not any("logo" in p for p in photos), "a CDN-served logo survived the junk filter"
    assert photos[0].endswith("file=storefront.jpg")


def test_promo_in_the_QUERY_still_loses_its_penalty_race():
    """Same hole, ranking side: campaign artwork served through a CDN query
    must keep the -5 that pushes it out of the hero slot."""
    assert _photo_score("https://example.com/cdn?file=giveaway-banner.jpg") < 0
    assert _photo_score("https://example.com/cdn?file=storefront.jpg") > 0


def test_query_scoping_did_not_re_break_the_host_case():
    """The host must still be invisible to both filters — that is the original
    bug, and widening the scope back to path+query must not undo it."""
    for host in TRAP_HOSTS:
        assert extract_photos(HTML, f"https://{host}"), f"{host} regressed"
    assert _photo_score("https://promoplumbing.com/img/storefront.jpg") > 0


def test_extension_bonus_survives_a_query_string():
    """`.jpg?v=2` is still a JPEG. The extension check is $-anchored, so it
    reads the bare path — widening it to path+query would silently zero this."""
    assert _photo_score("https://example.com/img/photo.jpg?v=2") == _photo_score(
        "https://example.com/img/photo.jpg"
    )
