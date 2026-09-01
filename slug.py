"""
LVRG Engine — Slug

The one and only place a domain becomes a preview slug. Three copies of this
logic used to exist (api.py, run_engine.py, and v2's api.py) and they disagreed,
which is how a folder literally named `www` ended up in the previews repo.
Do not inline another.
"""

import hashlib
import re
from urllib.parse import urlsplit

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


def make_slug(domain: str, page_url: str = "", variant: str = "") -> str:
    """Domain (+ optional sub-page, + optional variant) -> preview slug.

    Slugs the FULL domain, so foo.com and foo.net no longer collide on `foo`
    and silently overwrite each other's live preview. Matches the format the
    docs have always claimed: acmedental.com -> acmedental-com.

    The mapping must be INJECTIVE — two different domains sharing a slug share a
    folder in the previews repo, and the second build silently overwrites the
    first business's live page. Three ways that used to happen are handled below.

    `page_url` distinguishes two businesses that share one domain (POD01-34).
    Without it, a charity's restaurant and its laundry arm both slug to
    `ateliers--atbs-fr` and the second build overwrites the first's live page.

    `variant` distinguishes two businesses that share one domain AND have no
    distinguishing path — the branches of a chain. Every Better Buzz location
    lists `betterbuzzcoffee.com`, so page_url is empty for all of them and they
    all slug to `betterbuzzcoffee-com`. Building the second one silently
    replaced the first's live page, after that link had already been emailed.
    The caller passes something that differs per branch (the business name, which
    Google already qualifies by location).

    Both suffixes are OPTIONAL and omitting them returns the domain-only slug
    BYTE-IDENTICALLY, so no existing preview is orphaned and root-domain leads
    behave exactly as they do today. That property is what lets the caller give
    the suffix only to the branch that has no live page yet, leaving the one
    already in a prospect's inbox untouched.
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

    # ── Sub-page suffix ──────────────────────────────────────────────────────
    #
    # `---` is the separator because it is UNREACHABLE from the domain half, so
    # the join stays injective and the slug is still unambiguously splittable:
    #
    #   * slugify collapses any run of separators, so a slugified label never
    #     contains `--` and never starts or ends with `-`
    #   * `.replace("-", "--")` therefore turns each single hyphen into exactly
    #     `--`, never three
    #   * labels are joined with a single `-`, and since no label ends or starts
    #     with a hyphen, the join cannot butt up against a `--` to make `---`
    #
    # Same reasoning for the suffix itself: slugify output holds no `--`, so the
    # FIRST `---` in a slug is always the domain/path boundary.
    # Budget the suffix against the consumer's ceiling. leadscraper's
    # serve-smart-site rejects anything failing /^[a-z0-9][a-z0-9_-]{0,128}$/,
    # so a slug over 128 chars deploys fine and then 400s on the preview link —
    # a dead URL in a sent email. The domain half is never touched (that would
    # orphan published previews), so the suffix absorbs the limit.
    path_slug = _path_slug(page_url, MAX_SLUG_CHARS - len(s) - len(BOUNDARY))
    if path_slug:
        s = f"{s}{BOUNDARY}{path_slug}"

    # ── Variant suffix ───────────────────────────────────────────────────────
    #
    # Appended with the same boundary, so a slug carrying both reads
    # `domain---path---variant`. Still unambiguously splittable: no fragment can
    # contain `---` (see the reasoning above), so every `---` is a boundary.
    #
    # The variant fragment shares a NAMESPACE with the path fragment — both sit
    # after a `---` — so a readable variant alone is not safe. A branch named
    # "Hillcrest" and a sibling page at `/hillcrest` would both yield
    # `betterbuzzcoffee-com---hillcrest`: the exact overwrite this argument
    # exists to prevent, reintroduced one level down. `_variant_slug` therefore
    # ALWAYS carries a digest, which a faithful path fragment never does.
    variant_slug = _variant_slug(variant, page_url, MAX_SLUG_CHARS - len(s) - len(BOUNDARY))
    if variant_slug:
        s = f"{s}{BOUNDARY}{variant_slug}"

    return s


# Long paths would push the slug past what a tidy directory name should be, and
# the tail of a URL path is rarely what distinguishes two sibling businesses.
# Truncation is safe for the folder name but NOT for uniqueness on its own —
# see the note in _path_slug.
MAX_PATH_SLUG_CHARS = 40

# The separator between the domain half and the path half. Three hyphens because
# the domain half can never produce them — see the reasoning in make_slug.
BOUNDARY = "---"

# leadscraper's serve-smart-site validates the slug with
# /^[a-z0-9][a-z0-9_-]{0,128}$/ before proxying GitHub Pages, so 129 characters
# is a hard ceiling: a longer slug builds and deploys, then 400s on the preview
# link that has already been emailed.
MAX_SLUG_CHARS = 128

# `-` + 6 hex. Reserved whenever a digest is required so truncation can never
# clip the very thing that makes the fragment unique.
DIGEST_CHARS = 7


def _unslug_path(readable: str) -> str:
    """Inverse of the segment encoding below: slug fragment -> original path.

    Exists so the encoder can PROVE a readable fragment determines exactly one
    path. If this round-trips, the fragment is safe to use alone; if it does not,
    the caller attaches a digest instead of hoping.
    """
    holder = "\x00"
    parts = readable.replace("--", holder).split("-")
    return "/".join(p.replace(holder, "-") for p in parts)


def _variant_slug(variant: str, page_url: str = "", budget: int = MAX_PATH_SLUG_CHARS) -> str:
    """A caller-supplied discriminator as a slug fragment, or "" if absent.

    For businesses that share a domain and have no distinguishing path — chain
    branches. `variant` is whatever the caller knows differs; leadscraper sends
    the business name, because Google already qualifies branches by location
    ("Better Buzz Coffee Hillcrest").

    ALWAYS carries a digest, unlike `_path_slug`, which drops it when the
    readable form round-trips. Two reasons, and both are collisions this
    function exists to prevent:

      * It shares a namespace with the path fragment. A branch named "Hillcrest"
        and a sibling page at `/hillcrest` both read as `hillcrest`, so a
        readable-only variant could collide with a path on the same domain.
        A faithful path fragment never carries a digest, so a digest here is
        enough to separate the two namespaces for good.
      * slugify is lossy the same way it is for paths — it folds case and
        transliterates — so "Café Nord" and "Cafe Nord" read identically. The
        digest is over the RAW variant, so they stay distinct.

    The digest is salted with `page_url` so the same branch name under two
    different sub-pages cannot converge either.
    """
    if not variant:
        return ""
    raw = variant.strip()
    if not raw:
        return ""

    # NOT doubled, unlike the domain labels and path segments. Those are JOINED
    # from several parts with a single `-`, so a literal hyphen inside a part has
    # to be distinguishable from the join separator. A variant is one string with
    # nothing to join, and slugify never emits `--`, so `---` is already
    # unreachable here and doubling would only turn "Better Buzz Coffee
    # Hillcrest" into `better--buzz--coffee--hillcrest` on a URL a prospect sees.
    #
    # Injectivity does not depend on the readable half anyway — the digest below
    # is over the RAW variant, so "Better Buzz" and "Better-Buzz" stay distinct
    # even though they slugify the same.
    readable = slugify(raw)

    digest = hashlib.sha1(f"{page_url}\x00{raw}".encode("utf-8")).hexdigest()[:6]
    limit = min(MAX_PATH_SLUG_CHARS, budget)
    head = readable[: max(0, limit - DIGEST_CHARS)].strip("-")
    if not head:
        # No room for anything readable, or the variant slugified to nothing at
        # all (punctuation only). The digest alone still identifies it uniquely,
        # which is the property that actually matters.
        return digest
    return f"{head}-{digest}"


def _path_slug(page_url: str, budget: int = MAX_PATH_SLUG_CHARS) -> str:
    """The path portion of a URL as a slug fragment, or "" if there isn't one.

    Returns "" for a root URL (with or without a query string), which is what
    keeps root-domain leads byte-identical to the old single-argument behaviour.

    INJECTIVE, and it has to be for the same reason the domain half does — two
    sub-businesses sharing a fragment share a preview folder and the second build
    silently overwrites the first's live page.

    The naive version was `slugify(path)`, and a sweep caught it collapsing three
    genuinely different pages onto one fragment: `/menu/lunch`, `/menu-lunch` and
    `/menu--lunch` all became `menu-lunch`. So the path gets the same treatment
    the domain labels get — slugify each SEGMENT, double any literal hyphen
    inside it, join with a single hyphen — which separates `/` from `-`.

    That still is not enough on its own, because slugify is lossy in ways the
    encoding cannot express: it folds case (`/Menu`) and transliterates
    (`/café`), and paths are case-sensitive on most servers. So the fragment is
    round-tripped through `_unslug_path` and compared to the original. Faithful
    means it stands alone; lossy means a digest of the exact path is appended.
    Either way exactly one path maps to any fragment, and the common case
    (`/restauration`) stays clean and readable.
    """
    if not page_url:
        return ""
    raw = page_url.strip()
    if not raw:
        return ""
    # urlsplit only recognises a path after a scheme; a bare `acme.com/x` parses
    # entirely as the path, which would put the host into the suffix.
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        path = urlsplit(raw).path
    except ValueError:
        return ""

    # Case and original characters preserved — the faithfulness test below
    # compares against this, so lowering it here would make `/Menu` look
    # identical to `/menu` and reintroduce the collision.
    normalized = path.strip("/")
    if not normalized:
        return ""

    segments = [seg for seg in (slugify(s) for s in normalized.split("/")) if seg]
    if not segments:
        return ""
    readable = "-".join(seg.replace("-", "--") for seg in segments)

    # The clean case: the fragment round-trips to exactly this path, and it fits.
    # No digest, so the folder name stays legible in the previews repo.
    limit = min(MAX_PATH_SLUG_CHARS, budget)
    if _unslug_path(readable) == normalized and len(readable) <= limit:
        return readable

    # Otherwise the fragment cannot stand alone — it is either lossy (case folded,
    # transliterated, `--` collapsed) or too long — so pin it to the exact path
    # with a digest. Reserved space first, so the clip can never eat the digest
    # and turn a unique fragment back into a colliding one.
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:6]
    head = readable[:max(0, limit - DIGEST_CHARS)].strip("-")
    if not head:
        # No room for anything readable. The digest alone still identifies the
        # path uniquely, which is the property that actually matters.
        return digest
    return f"{head}-{digest}"
