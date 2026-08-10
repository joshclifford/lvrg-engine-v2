"""
LVRG Engine — Slug

The one and only place a domain becomes a preview slug. Three copies of this
logic used to exist (api.py, run_engine.py, and v2's api.py) and they disagreed,
which is how a folder literally named `www` ended up in the previews repo.
Do not inline another.
"""

from slugify import slugify


def make_slug(domain: str) -> str:
    """Domain -> preview slug.

    Slugs the FULL domain, so foo.com and foo.net no longer collide on `foo`
    and silently overwrite each other's live preview. Matches the format the
    docs have always claimed: acmedental.com -> acmedental-com.
    """
    d = (domain or "").strip().lower()
    d = d.removeprefix("https://").removeprefix("http://")
    d = d.split("/")[0].split("?")[0].split("#")[0]
    d = d.split("@")[-1].split(":")[0]
    # removeprefix, not lstrip — lstrip('www.') strips any leading w or dot,
    # so www.wine.com became `ine` and a bare `www.` became an empty slug.
    d = d.removeprefix("www.")

    s = slugify(d)
    if not s:
        raise ValueError(f"empty slug for domain {domain!r}")
    return s
