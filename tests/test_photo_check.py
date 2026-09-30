"""Photos are judged by what they are, not by their file name (POD01-268/269).

Two failures, one cause. Garbuilders' Get Listed published four 512x512
line-art icons as full-width photos because they had ordinary .png names.
Felicia Lewis Group's came out with no photos at all because its site
(Luxury Presence) serves every photo from an address with no extension, and
the filter threw away anything not ending in .jpg/.png/.webp.

Now any <img> is a candidate unless its name says it cannot be a photo, and
what gets published is what passes a check on the downloaded bytes.
"""

import struct

import intel
from test_photo_inlining import _Resp, _serve


def _png(w, h, colour, size):
    head = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">IIBBBBB", w, h, 8, colour, 0, 0, 0))
    return head + b"\0" * max(0, size - len(head))


def _jpeg(w, h, size):
    head = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
            + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, h, w))
    return head + b"\0" * max(0, size - len(head))


def _webp(w, h, size):
    head = (b"RIFF" + struct.pack("<I", size) + b"WEBPVP8X" + struct.pack("<I", 10)
            + b"\0" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little"))
    return head + b"\0" * max(0, size - len(head))


ICON = _png(512, 512, 6, 8_000)                 # Garbuilders: RGBA, 0.03 B/px
BADGE = _png(500, 500, 3, 60_000)               # Felicia: palette award badge
PHOTO = _jpeg(1280, 853, 160_000)               # Felicia: a real team photo


# ── the check on the downloaded bytes ───────────────────────────────────────

def test_a_flat_transparent_icon_is_not_a_photo():
    assert "flat PNG" in intel._not_a_photo("image/png", ICON)


def test_a_palette_png_logo_or_badge_is_not_a_photo():
    assert "palette" in intel._not_a_photo("image/png", BADGE)


def test_a_real_jpeg_photo_passes():
    assert intel._not_a_photo("image/jpeg", PHOTO) == ""


def test_a_real_lossless_png_photo_passes():
    assert intel._not_a_photo("image/png", _png(800, 600, 2, 400_000)) == ""


def test_a_webp_photo_passes():
    assert intel._not_a_photo("image/webp", _webp(1280, 800, 120_000)) == ""


def test_a_thumbnail_is_too_small():
    assert "too small" in intel._not_a_photo("image/jpeg", _jpeg(200, 150, 9_000))


def test_gifs_and_svgs_are_never_photos():
    assert intel._not_a_photo("image/gif", b"GIF89a") == "image/gif"
    assert intel._not_a_photo("image/svg+xml", b"<svg/>") == "image/svg+xml"


def test_an_image_it_cannot_measure_is_kept_as_before():
    assert intel._not_a_photo("image/jpeg", b"\xff\xd8\xffhello") == ""


# ── candidates with no file extension ───────────────────────────────────────

LP = "https://media-production.lp-cdn.com/cdn-cgi/image/{opts}/https://media-production.lp-cdn.com/media/d4e8ad90"
SIZED = LP.format(opts=intel._CF_RESIZE_OPTIONS)


def test_an_extensionless_cdn_photo_is_a_candidate():
    html = f'<img src="{LP.format(opts="format=auto,quality=85")}">'
    assert intel.extract_photos(html, "https://felicialewisgroup.com") == [SIZED]


def test_src_and_srcset_copies_of_one_photo_become_one_sized_url():
    html = (f'<img src="{LP.format(opts="format=auto,quality=85,width=1280")}" '
            f'srcset="{LP.format(opts="format=auto,quality=85,width=320")} 320w">')
    assert intel.extract_photos(html, "https://felicialewisgroup.com") == [SIZED]


def test_non_photo_files_and_tracking_pixels_are_still_skipped():
    html = ('<img src="/a.svg"><img src="/b.gif"><img src="/c.ico">'
            '<img src="https://www.facebook.com/tr?id=1&ev=PageView&noscript=1">')
    assert intel.extract_photos(html, "https://example.com") == []


# ── downloading and choosing ────────────────────────────────────────────────

def test_icons_are_dropped_and_real_photos_published(monkeypatch):
    icon, photo = "https://garbuilders.com/i/pool.png", "https://p.com/team.jpg"
    _serve(monkeypatch, {icon: _Resp(ctype="image/png", body=ICON),
                         photo: _Resp(body=PHOTO)})
    dropped = set()
    out = intel.fetch_photo_assets([icon, photo], dropped)
    assert list(out) == [photo]
    assert dropped == {icon}


def test_it_looks_past_the_logos_to_find_the_photos(monkeypatch):
    """Felicia's first several images are badges; the photos come after."""
    badges = [f"https://cdn.lp.com/media/badge{i}" for i in range(6)]
    photos = [f"https://cdn.lp.com/media/photo{i}" for i in range(3)]
    responses = {u: _Resp(ctype="image/png", body=BADGE) for u in badges}
    responses.update({u: _Resp(body=PHOTO) for u in photos})
    _serve(monkeypatch, responses)
    dropped = set()
    out = intel.fetch_photo_assets(badges + photos, dropped)
    assert list(out) == photos
    assert dropped == set(badges)


def test_an_extensionless_url_that_will_not_download_is_dropped(monkeypatch):
    """Nothing says it is an image, so it must not be hotlinked either."""
    dead = "https://cdn.lp.com/media/abc"
    _serve(monkeypatch, {})
    dropped = set()
    assert intel.fetch_photo_assets([dead], dropped) == {}
    assert dropped == {dead}


def test_a_jpg_that_will_not_download_still_falls_back_to_its_url(monkeypatch):
    """POD01-124's rule stands for a named photo: hotlinked, not dropped."""
    dead = "https://p.com/storefront.jpg"
    _serve(monkeypatch, {})
    dropped = set()
    assert intel.fetch_photo_assets([dead], dropped) == {}
    assert dropped == set()
