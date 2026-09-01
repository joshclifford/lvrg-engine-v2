"""A slug collision means one business silently overwrites another's live page.

`make_slug` maps a domain to a folder in the previews repo. If two domains map
to the same slug, the second build replaces the first business's published site
— under a link that has already been emailed.

Two collisions were reproduced against this code on 2026-08-12:

    foo.bar.com  and  foo-bar.com          -> both `foo-bar-com`
    münchen.de   and  xn--mnchen-3ya.de    -> two slugs for ONE domain

The first is the same failure B2 fixed for foo.com / foo.net, one shape over.
"""

import pytest

from slug import make_slug


# --------------------------------------------------------------------------
# the format the docs promise, and the shapes already handled
# --------------------------------------------------------------------------

@pytest.mark.parametrize("domain,expected", [
    ("acmedental.com", "acmedental-com"),
    ("ACMEDENTAL.COM", "acmedental-com"),
    ("https://acmedental.com", "acmedental-com"),
    ("http://www.acmedental.com/some/path?q=1#frag", "acmedental-com"),
    ("  acmedental.com  ", "acmedental-com"),
    ("acmedental.com:8080", "acmedental-com"),
    ("user@acmedental.com", "acmedental-com"),
])
def test_documented_format_is_unchanged(domain, expected):
    """A hyphen-free domain must keep the slug the docs have always claimed."""
    assert make_slug(domain) == expected


def test_www_prefix_does_not_eat_letters():
    """lstrip('www.') turned www.wine.com into `ine`."""
    assert make_slug("www.wine.com") == "wine-com"


def test_repeated_www_is_fully_stripped():
    """One pass left `www-acme-com` — the folder name this function exists to avoid."""
    assert make_slug("www.www.acme.com") == "acme-com"
    assert make_slug("www.www.www.acme.com") == "acme-com"


def test_www_is_not_stripped_off_a_real_domain():
    """www.com is a live registration — stripping it would leave nothing."""
    assert make_slug("www.com") == "www-com"


def test_full_domain_is_slugged():
    """foo.com and foo.net must not collide on `foo`."""
    assert make_slug("foo.com") != make_slug("foo.net")


def test_empty_slug_is_refused():
    for bad in ["", "www.", "https://", None]:
        with pytest.raises(ValueError):
            make_slug(bad)


# --------------------------------------------------------------------------
# E2 — a dot and a hyphen must not produce the same slug
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("foo.bar.com",       "foo-bar.com"),
    ("clinic.acme.com",   "clinic-acme.com"),
    ("shop.dental.co.uk", "shop-dental.co.uk"),
    ("a.b.c.com",         "a-b-c.com"),
    ("www.foo.bar.com",   "foo-bar.com"),
])
def test_subdomain_does_not_collide_with_hyphen(a, b):
    assert make_slug(a) != make_slug(b), (
        f"{a} and {b} share a slug — one would overwrite the other's live preview"
    )


def test_hyphenated_domain_round_trips_distinctly():
    """Doubling is what makes it injective: `--` is unreachable from a dot."""
    assert make_slug("foo-bar.com") == "foo--bar-com"
    assert make_slug("foo.bar.com") == "foo-bar-com"


# --------------------------------------------------------------------------
# E3 — one domain, two encodings, one slug
# --------------------------------------------------------------------------

@pytest.mark.parametrize("unicode_form,punycode_form", [
    ("münchen.de",     "xn--mnchen-3ya.de"),
    ("café-dental.fr", "xn--caf-dental-d7a.fr"),
    ("十二.com",        "xn--4kqp8i.com"),
])
def test_idn_and_punycode_agree(unicode_form, punycode_form):
    assert make_slug(unicode_form) == make_slug(punycode_form), (
        "the same domain in two encodings produced two slugs — two builds, two charges"
    )


# --------------------------------------------------------------------------
# the property that actually matters
# --------------------------------------------------------------------------

def test_no_collisions_across_a_realistic_set():
    domains = [
        "acmedental.com", "acmedental.net", "acme-dental.com", "acme.dental.com",
        # NB: no `www.` variants here — `www.` is stripped on purpose, so
        # www.foo-bar.com and foo-bar.com SHOULD share a slug. That is covered
        # by test_www_prefix_does_not_eat_letters, not by this uniqueness set.
        "foo.com", "foo.net", "foo.bar.com", "foo-bar.com",
        "clinic.acme.com", "clinic-acme.com", "shop.dental.co.uk",
        "shop-dental.co.uk", "a.b.c.com", "a-b-c.com", "a-b.c.com", "a.b-c.com",
        "münchen.de", "wine.com", "3ddigital.com", "787coffee.com",
    ]
    slugs = {}
    for d in domains:
        s = make_slug(d)
        assert s not in slugs, f"collision: {d} and {slugs[s]} both -> {s}"
        slugs[s] = d


# --------------------------------------------------------------------------
# POD01-34 — one domain, several businesses
#
# `extractDomain` / `scrape_site` used to flatten the path, so a business living
# inside a larger site got the PARENT scraped and published. The fix carries the
# lead's own page as `page_url`, which means the slug now has to tell two pages
# on ONE domain apart — otherwise the second build overwrites the first's live
# preview, the same failure E2 fixed for dots vs hyphens.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("page_url", [
    "",
    "https://acmedental.com",
    "https://acmedental.com/",
    "https://www.acmedental.com/",
    "https://acmedental.com//",
    "https://acmedental.com/?utm_source=x",
    "https://acmedental.com/#booking",
])
def test_root_page_url_is_byte_identical_to_no_page_url(page_url):
    """Acceptance criterion: a lead with no path behaves exactly as today.

    This is what stops every already-published preview from being orphaned — a
    root-domain lead must keep the folder its emailed link already points at.
    """
    assert make_slug("acmedental.com", page_url) == make_slug("acmedental.com")
    assert make_slug("acmedental.com", page_url) == "acmedental-com"


def test_sub_page_gets_its_own_slug():
    """The real case: a restaurant inside a charity's site (lead 39923c42-…)."""
    parent = make_slug("ateliers-atbs.fr")
    restaurant = make_slug("ateliers-atbs.fr", "https://www.ateliers-atbs.fr/restauration/")
    assert parent == "ateliers--atbs-fr"
    assert restaurant == "ateliers--atbs-fr---restauration"
    assert parent != restaurant


def test_siblings_on_one_domain_do_not_collide():
    """Two sub-businesses of the same parent must not share a preview folder."""
    slugs = {
        p: make_slug("ateliers-atbs.fr", f"https://ateliers-atbs.fr{p}")
        for p in ["/restauration/", "/blanchisserie/", "/espaces-verts/", "/floral"]
    }
    assert len(set(slugs.values())) == len(slugs), f"collision among {slugs}"


@pytest.mark.parametrize("a,b", [
    # A slash and a hyphen must not collapse together — the path half of E2.
    ("/menu/lunch",  "/menu-lunch"),
    ("/menu-lunch",  "/menu--lunch"),
    ("/menu/lunch",  "/menu--lunch"),
    ("/en/menu",     "/en-menu"),
    # Paths are case-sensitive on most servers, unlike domains.
    ("/menu",        "/Menu"),
    # slugify transliterates, so these two are different pages.
    ("/café",        "/cafe"),
])
def test_distinct_paths_do_not_collide(a, b):
    """All six of these pairs collapsed onto one slug in the first cut of the fix.

    `slugify(path)` folds `/`, `-`, case and accents all onto the same character
    set, so three genuinely different pages landed on `menu-lunch`. Caught by a
    sweep before it shipped, not after.
    """
    sa = make_slug("acme.com", f"https://acme.com{a}")
    sb = make_slug("acme.com", f"https://acme.com{b}")
    assert sa != sb, f"{a} and {b} both -> {sa}"


def test_trailing_slash_is_the_same_page():
    """`/menu` and `/menu/` are one resource — they SHOULD share a slug."""
    assert (make_slug("acme.com", "https://acme.com/menu")
            == make_slug("acme.com", "https://acme.com/menu/"))


def test_long_paths_sharing_a_prefix_stay_distinct():
    """The length clip must not become a collision.

    Two paths identical for their first 40 characters would otherwise land on
    one folder, which is the overwrite this suffix exists to prevent.
    """
    base = "https://acme.com/" + "a" * 60
    assert make_slug("acme.com", f"{base}/one") != make_slug("acme.com", f"{base}/two")


def test_readable_form_survives_for_the_common_case():
    """A digest on every slug would be injective but unreadable in the repo.

    Plain paths must stay legible; only lossy ones pay for a digest.
    """
    assert make_slug("clinic.org", "https://clinic.org/departments/cardiology") == (
        "clinic-org---departments-cardiology"
    )


def test_slug_stays_within_the_preview_proxy_ceiling():
    """leadscraper's serve-smart-site rejects a slug over 128 chars.

    A longer slug deploys fine and then 400s on the preview link that has already
    been emailed, so the suffix has to absorb the limit — the domain half cannot
    shrink without orphaning previews that are already published.
    """
    long_domain = "-".join(["averylongbusinessname"] * 3) + ".example.co.uk"
    for path in ["/x", "/departments/cardiology", "/" + "a" * 200,
                 "/" + "/".join(["segment"] * 30)]:
        s = make_slug(long_domain, f"https://{long_domain}{path}")
        assert len(s) <= 128, f"{len(s)} chars for {path}: {s}"


def test_long_domain_paths_still_do_not_collide():
    """Shrinking the suffix must not reintroduce the overwrite it prevents."""
    long_domain = "-".join(["averylongbusinessname"] * 3) + ".example.co.uk"
    a = make_slug(long_domain, f"https://{long_domain}/departments/cardiology")
    b = make_slug(long_domain, f"https://{long_domain}/departments/oncology")
    assert a != b
    assert len(a) <= 128 and len(b) <= 128


def test_domain_only_slug_never_contains_the_boundary():
    """`---` must mean exactly one thing: the domain/path split.

    If a domain could produce `---`, the boundary would be ambiguous and two
    different (domain, path) pairs could render the same string.
    """
    for d in ["foo-bar.com", "a-b.com", "münchen.de", "a.b.c.com",
              "shop-dental.co.uk", "www.www.acme.com", "x--y.com"]:
        assert "---" not in make_slug(d), d


# ── variant: chain branches sharing one domain ───────────────────────────────
#
# Every Better Buzz location lists betterbuzzcoffee.com and none of them has a
# distinguishing path, so all of them slugged to `betterbuzzcoffee-com` and the
# second build silently replaced the first's live page — after that link had
# already been emailed. `variant` is the caller's discriminator.


def test_no_variant_is_byte_identical():
    """The regression guard for every preview already published.

    Nothing may move for a lead that does not pass a variant, or links sitting
    in prospects' inboxes start 404ing.
    """
    for d in ["acmedental.com", "foo-bar.com", "münchen.de", "www.acme.com"]:
        assert make_slug(d, "", "") == make_slug(d)
        assert make_slug(d, "", None or "") == make_slug(d)
    # With a path, too — the variant is appended after it, not instead of it.
    assert (make_slug("ateliers-atbs.fr", "https://ateliers-atbs.fr/restauration", "")
            == make_slug("ateliers-atbs.fr", "https://ateliers-atbs.fr/restauration"))


def test_two_branches_of_one_chain_do_not_collide():
    """The bug this argument exists for."""
    a = make_slug("betterbuzzcoffee.com", "", "Better Buzz Coffee Hillcrest")
    b = make_slug("betterbuzzcoffee.com", "", "Better Buzz Coffee Mission Gorge & Zion")
    assert a != b
    assert a.startswith("betterbuzzcoffee-com---")
    assert b.startswith("betterbuzzcoffee-com---")


def test_variant_never_collides_with_a_path_of_the_same_name():
    """Both suffixes share one namespace, so a readable variant is not enough.

    A branch called "Hillcrest" and a sibling page at `/hillcrest` would both
    render `betterbuzzcoffee-com---hillcrest` — the same overwrite, one level
    down. The variant always carries a digest; a faithful path never does.
    """
    as_path = make_slug("betterbuzzcoffee.com", "https://betterbuzzcoffee.com/hillcrest")
    as_variant = make_slug("betterbuzzcoffee.com", "", "Hillcrest")
    assert as_path != as_variant
    assert as_path == "betterbuzzcoffee-com---hillcrest"


def test_variant_survives_lossy_slugification():
    """slugify folds case and transliterates, so the digest is over the raw text."""
    assert make_slug("acme.com", "", "Café Nord") != make_slug("acme.com", "", "Cafe Nord")
    assert make_slug("acme.com", "", "Hillcrest") != make_slug("acme.com", "", "hillcrest")


def test_variant_composes_with_a_path():
    """A sub-page business that is ALSO one of several branches."""
    base = "https://ateliers-atbs.fr/restauration"
    a = make_slug("ateliers-atbs.fr", base, "Nord")
    b = make_slug("ateliers-atbs.fr", base, "Sud")
    path_only = make_slug("ateliers-atbs.fr", base)
    assert a != b != path_only and a != path_only
    # domain---path---variant: every `---` is a boundary, so it stays splittable.
    assert a.count("---") == 2


def test_same_variant_under_different_paths_stays_distinct():
    """The digest is salted with page_url, so "Nord" twice is still two folders."""
    a = make_slug("acme.com", "https://acme.com/restaurant", "Nord")
    b = make_slug("acme.com", "https://acme.com/laundry", "Nord")
    assert a != b


def test_variant_respects_the_128_char_ceiling():
    """serve-smart-site 400s past 128, on a link that has already been sent."""
    long_domain = "-".join(["averylongbusinessname"] * 3) + ".example.co.uk"
    for v in ["Nord", "A" * 200, "Mission Gorge & Zion — Second Floor, Suite 400"]:
        s = make_slug(long_domain, f"https://{long_domain}/departments/cardiology", v)
        assert len(s) <= 128, f"{len(s)} chars for {v!r}: {s}"


def test_punctuation_only_variant_still_discriminates():
    """slugify("!!!") is empty, so the readable half vanishes — the digest cannot."""
    a = make_slug("acme.com", "", "!!!")
    b = make_slug("acme.com", "", "???")
    assert a != b
    assert a != make_slug("acme.com")


def test_variant_fragment_never_contains_the_boundary():
    """`---` must stay unambiguous even though the variant is not hyphen-doubled.

    slugify collapses any run of separators, so its output holds no `--` at all.
    Doubling would only make a URL a prospect sees uglier.
    """
    for v in ["Better Buzz Coffee Hillcrest", "A - B", "a--b", "  spaced  out  ",
              "Mission Gorge & Zion", "Café — Nord", "x---y"]:
        s = make_slug("acme.com", "", v)
        assert s.count("---") == 1, f"{v!r} -> {s}"


def test_variant_differing_only_by_punctuation_stays_distinct():
    """The digest is over the raw text, so the readable half may collapse."""
    a = make_slug("acme.com", "", "Better Buzz")
    b = make_slug("acme.com", "", "Better-Buzz")
    assert a != b
