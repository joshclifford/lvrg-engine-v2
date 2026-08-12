"""
LVRG Engine — Slug

The one and only place a domain becomes a preview slug. Three copies of this
logic used to exist (api.py, run_engine.py, and v2's api.py) and they disagreed,
which is how a folder literally named `www` ended up in the previews repo.
Do not inline another.
"""

import re

from slugify import slugify


def canonical_domain(domain: str) -> str:
    """A domain reduced to its comparable form: host only, lowercased.

    `https://www.acme.com/?utm_source=x` and `acme.com` are the same business,
    and anything that stores or compares the raw string treats them as two.

    This MIRRORS lm-tool's `normalizeDomain` in lib/leads.ts. Both sides have to
    agree or reply routing splits one business in half again — 6-16 of 142 rows
    in lm-tool carry a protocol, a `www.` or a trailing slash today because the
    engine wrote whatever string it was handed.

    Returns "" for input that is not a domain, which callers must handle.
    """
    d = (domain or "").strip().lower()
    if not d:
        return ""
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d)   # protocol
    d = d.split("/")[0].split("?")[0].split("#")[0]  # path, query, fragment
    d = d.split("@")[-1].split(":")[0]            # userinfo, port
    # Loop, and only while a label would remain: `www.com` is a live
    # registration, so stripping it would leave a bare TLD.
    while d.startswith("www."):
        rest = d[4:]
        if rest and "." not in rest:
            break
        d = rest
    return d.rstrip(".")


def make_slug(domain: str) -> str:
    """Domain -> preview slug.

    Slugs the FULL domain, so foo.com and foo.net no longer collide on `foo`
    and silently overwrite each other's live preview. Matches the format the
    docs have always claimed: acmedental.com -> acmedental-com.

    The mapping must be INJECTIVE — two different domains sharing a slug share a
    folder in the previews repo, and the second build silently overwrites the
    first business's live page. Two ways that used to happen are handled below.
    """
    # One canonicaliser, shared with everything that has to agree on what "the
    # same domain" means. Inlining a second copy of these rules is how three
    # slug implementations once drifted apart; the same applies here.
    #
    # It handles: protocol, path, query, fragment, userinfo, port, and repeated
    # `www.` — the last with a guard, since `www.com` is a live registration and
    # a bare `www.` correctly strips to empty and raises below.
    d = canonical_domain(domain)

    # Collapse an internationalised domain to its single canonical form first.
    # münchen.de and xn--mnchen-3ya.de are the SAME domain written two ways, and
    # slugifying them directly produced `munchen-de` and `xn-mnchen-3ya-de` — two
    # slugs, two folders, two builds, two charges for one business.
    #
    # Bare except on UnicodeError: the idna codec also rejects empty labels
    # ("acme..com"), a trailing dot, and labels over 63 chars. None of those are
    # worth failing a build over, so they fall through to slugify as before.
    try:
        d = d.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass

    # A dot becomes a hyphen, so a literal hyphen has to become something a dot
    # can never produce — otherwise foo.bar.com and foo-bar.com both land on
    # `foo-bar-com` and overwrite each other. Doubling is the punycode trick:
    # `--` is unreachable from a dot, so the mapping is injective.
    #
    # Hyphen-free domains are untouched, which is the overwhelming majority:
    # acmedental.com is still acmedental-com. Only a domain that actually
    # contains a hyphen changes shape.
    labels = [slugify(label) for label in d.split(".")]
    s = "-".join(label.replace("-", "--") for label in labels if label)

    if not s:
        raise ValueError(f"empty slug for domain {domain!r}")
    return s
