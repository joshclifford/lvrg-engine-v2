"""Junk-image words must match as tokens, not as substrings.

Stripping the HOST fixed the domain-level case on 2026-08-11 — badgerroofing.com
stopped losing every photo to "badge". The same defect survived one level down,
in the PATH, where an ordinary filename containing a junk substring was still
dropped silently:

    /images/iconic-dental.jpg    lost to "icon"
    /photos/badger-team.jpg      lost to "badge"
    /media/arrowhead-plaza.jpg   lost to "arrow"
    /gallery/blankenship.jpg     lost to "blank"

A dropped photo is invisible — the page just falls back to gradients — so this
needs a test rather than a reviewer noticing.
"""

import pytest

import intel

BASE = "https://example-dental.com/"


def _kept(path):
    html = f'<img src="{path}">'
    return bool(intel.extract_photos(html, BASE))


# --------------------------------------------------------------------------
# real photos whose filename merely CONTAINS a junk word
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,swallowed_by", [
    ("/images/iconic-dental-office.jpg",   "icon"),
    ("/photos/badger-team-2026.jpg",       "badge"),
    ("/media/arrowhead-plaza-exterior.jpg", "arrow"),
    ("/gallery/blankenship-interior.jpg",  "blank"),
    ("/img/patterson-clinic.jpg",          "pattern"),
    ("/uploads/iconography-studio.jpg",    "icon"),
    ("/photos/spinnaker-marina.jpg",       "spinner"),
])
def test_junk_word_inside_a_word_is_not_junk(path, swallowed_by):
    assert _kept(path), f"real photo dropped — {swallowed_by!r} matched inside a word"


# --------------------------------------------------------------------------
# actual junk must still be dropped, singular and plural
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/logo.png",
    "/site-logo.png",
    "/assets/logo-white.png",
    "/logos/partner.png",
    "/badges/award.png",
    "/icons/phone.png",
    "/img/favicon.png",
    "/tracking-pixel.png",
    "/1x1.png",
    "/placeholder.jpg",
    "/avatar.jpg",
    "/avatars/staff-1.jpg",
])
def test_real_junk_is_still_dropped(path):
    assert not _kept(path), f"junk image kept: {path}"


# --------------------------------------------------------------------------
# the host fix must not regress
# --------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "https://iconicdental.com",
    "https://badgerroofing.com",
    "https://blankslatecoffee.com",
    "https://arrowplumbing.com",
    "https://pixelproo.com",
])
def test_junk_word_in_the_host_is_still_ignored(host):
    html = '<img src="/photos/storefront.jpg">'
    assert intel.extract_photos(html, host + "/"), (
        f"{host} lost its photos to its own domain name again"
    )
