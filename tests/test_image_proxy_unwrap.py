"""POD01-17 — photos served through a framework image optimiser.

Found while measuring the Playwright switch. Rendering texascreative.com (a
Next.js site) surfaced all 32 of its images, and extract_photos then returned
ZERO, because every src looked like:

    /_next/image?url=https%3A%2F%2Fapi.texascreative.com%2F...%2FTXC-40th.png&w=3840&q=75

_PHOTO_EXT requires the extension at the end of the string or before a `?`;
here it is followed by `&w=`. _JUNK_IMAGE meanwhile sees the opaque
`/_next/image` path and cannot tell a hero shot from a logo. Rendering the page
was necessary but not sufficient — the filter threw the photos away afterwards.
"""

import intel

BASE = "https://texascreative.com/"


# --------------------------------------------------------------------------
# Unwrapping
# --------------------------------------------------------------------------

def test_next_image_proxy_is_unwrapped_to_the_real_url():
    proxied = ("https://texascreative.com/_next/image"
               "?url=https%3A%2F%2Fapi.texascreative.com%2Ffiles%2FTXC-40th.png"
               "&w=3840&q=75")

    assert intel._unwrap_image_proxy(proxied) == \
        "https://api.texascreative.com/files/TXC-40th.png"


def test_same_host_proxy_path_is_resolved_against_the_host():
    proxied = "https://example.com/_next/image?url=%2Fuploads%2Fhero.jpg&w=828"

    assert intel._unwrap_image_proxy(proxied) == "https://example.com/uploads/hero.jpg"


def test_the_real_photo_survives_the_filter_once_unwrapped():
    """The end-to-end point: this page used to yield nothing."""
    html = ('<img src="/_next/image?url=https%3A%2F%2Fapi.texascreative.com'
            '%2Ffiles%2Fstorefront.png&w=3840&q=75">')

    assert intel.extract_photos(html, BASE) == \
        ["https://api.texascreative.com/files/storefront.png"]


def test_a_proxied_logo_is_still_filtered_out():
    """Unwrapping must feed the junk filter, not bypass it.

    Before unwrapping, _JUNK_IMAGE could only see `/_next/image` and waved
    everything through — so the fix that finds the photos would equally have
    promoted a logo into the hero slot.
    """
    html = ('<img src="/_next/image?url=https%3A%2F%2Fcdn.example.com'
            '%2Fassets%2Flogo.png&w=256&q=75">')

    assert intel.extract_photos(html, BASE) == []


def test_a_proxied_promo_banner_is_still_penalised():
    html = (
        '<img src="/_next/image?url=%2Fmedia%2Fgiveaway-banner.jpg&w=1920">'
        '<img src="/_next/image?url=%2Fmedia%2Fdining-room.jpg&w=1920">'
    )

    photos = intel.extract_photos(html, BASE)

    assert photos[0].endswith("dining-room.jpg"), \
        "a giveaway banner outranked the real photo"


# --------------------------------------------------------------------------
# What must NOT be unwrapped
# --------------------------------------------------------------------------

def test_a_plain_photo_url_is_untouched():
    for url in ("https://example.com/photos/hero.jpg",
                "https://example.com/photos/hero.jpg?v=2",
                "https://example.com/img"):
        assert intel._unwrap_image_proxy(url) == url


def test_a_non_url_query_value_is_not_treated_as_a_source():
    """`?url=` does not always carry a URL. Rewriting a legitimate src to a
    fragment of ad-tracking copy would be a new silent zero-photo failure."""
    url = "https://example.com/photos/hero.jpg?url=summer-sale&utm_source=fb"

    assert intel._unwrap_image_proxy(url) == url


def test_the_cdn_filename_convention_still_works():
    """`/cdn?file=photo.jpg` is a DIFFERENT shape, already relied on by
    test_scrape_edge_cases. `file` is deliberately not an unwrap parameter —
    that URL is already readable by _PHOTO_EXT as-is."""
    assert intel.extract_photos('<img src="/cdn?file=storefront.jpg">', BASE)
    assert not intel.extract_photos('<img src="/cdn?file=logo.png">', BASE)


def test_a_proxy_with_no_query_is_untouched():
    assert intel._unwrap_image_proxy("https://example.com/_next/image") == \
        "https://example.com/_next/image"


def test_malformed_urls_do_not_raise():
    for url in ("", "not a url", "https://", "https://example.com/?url=%%%"):
        intel._unwrap_image_proxy(url)
