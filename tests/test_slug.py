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
