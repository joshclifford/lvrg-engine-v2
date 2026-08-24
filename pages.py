"""Deterministic page-set planning for multi-page Smart Site builds.

Rule-based, not a Claude call — POD01-53's own acceptance criteria wants
deterministic, mockable behavior, and this pipeline is already timeout-tight
without adding a 6th sequential model round-trip ahead of the N per-page
calls it's deciding the shape of. A Claude-driven planner can be swapped in
later behind this same signature without touching any caller.
"""

import os

# Env-overridable, like SITE_MAX_TOKENS in generator.py — not a hard ceiling
# baked into the code, so it can be raised without a redeploy while this is
# being tried out. Smaller than the ticket's stated 5-6 ceiling on purpose:
# each extra page is another sequential Claude call inside a pipeline that
# already runs close to its downstream timeout at ONE page.
MAX_PAGES = int(os.environ.get("MAX_PAGES", "4"))

# Below this, a business's own description doesn't carry enough for a
# dedicated About page to say anything an already-thin Home page hasn't.
_ABOUT_DESCRIPTION_FLOOR = 80

# Below this, "Services" would be one card repeated — not a page.
_SERVICES_MIN_COUNT = 2


def plan_pages(intel: dict, max_pages: int = MAX_PAGES) -> list[dict]:
    """Decide which pages this build should generate, given what was actually
    scraped. Home and Contact are unconditional; About and Services are
    included only when intel supports them. Degrades gracefully to just
    [Home, Contact] for a thin business rather than inventing pages with
    nothing to say.

    Returns fresh dicts every call — this runs behind a thread-pool executor
    serving concurrent requests, and a shared mutable default would let one
    build's data leak into another's.

    Each page: {"slug": ..., "filename": ..., "title": ..., "brief": ...}.
    `brief` is a one-line pointer for generate_page's prompt — what this page
    covers and which scraped content it should draw from.
    """
    max_pages = max(2, max_pages)  # Home + Contact is the floor, always.

    description = (intel.get("description") or "").strip()
    press_mentions = intel.get("press_mentions") or []
    services = intel.get("services") or []
    business_name = intel.get("business_name", "the business")

    candidates = []
    if len(description) >= _ABOUT_DESCRIPTION_FLOOR or press_mentions:
        candidates.append({
            "slug": "about",
            "filename": "about.html",
            "title": "About",
            "brief": (
                f"The story of {business_name}: who they are, what makes them "
                f"different. Draw from the description and, if present, the "
                f"press mentions — do not repeat Home's hero copy verbatim."
            ),
        })
    if len(services) >= _SERVICES_MIN_COUNT:
        candidates.append({
            "slug": "services",
            "filename": "services.html",
            "title": "Services",
            "brief": (
                f"A dedicated page for the real services list: "
                f"{', '.join(services)}. One section per service or a clean "
                f"grid — more room than Home's condensed 3-card preview."
            ),
        })

    # Reserve Home + Contact's slots first, then fill the remaining budget —
    # never truncate from the end, which would silently drop Contact whenever
    # both optional pages qualify at a tight cap.
    budget = max(0, max_pages - 2)
    pages = [{
        "slug": "index",
        "filename": "index.html",
        "title": "Home",
        "brief": "The main landing page — hero, social proof, a preview of services, CTA.",
    }]
    pages.extend(candidates[:budget])
    pages.append({
        "slug": "contact",
        "filename": "contact.html",
        "title": "Contact",
        "brief": "Phone, hours, location, and a clear path to booking/contacting.",
    })
    return pages
