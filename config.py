"""
LVRG Lead Magnet Engine — Config
Reads from environment variables with fallbacks.
"""

import os

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
INSTANTLY_API_KEY = os.environ.get("INSTANTLY_API_KEY", "")

# Sender identity
SENDER_NAME = "Josh"
SENDER_EMAIL = "adam@mobiloptimismrade.com"
SENDER_AGENCY = "LVRG Agency"
SENDER_WEBSITE = "lvrg.com"
SENDER_PHONE = "619.361.7484"
# The "Let's Chat" booking page. /advertise/ 302s to the funnel HOMEPAGE
# (advertise.theresandiego.com/), which is a shop front, not a booking form —
# the prospect lands on a price list instead of a calendar. This is the link
# the campaigns were moved to; the engine was missed in that rollout, so every
# published preview and every generated email still pointed at the old one.
# Feeds the "Claim This Site" button on generated pages and the booking link
# in the outreach email prompt.
BOOKING_URL = "https://theresandiego.com/letschat"


def build_booking_url(offer: str, prospect_id: str = "") -> str:
    """BOOKING_URL carrying the attribution params for one generated page.

    Every CTA on every magnet used to point at the bare BOOKING_URL, so a
    booked call arrived with no way to tell which business clicked, which offer
    they had been shown, or which page they had read. Whoever picked up the call
    reconstructed all of it live, and nothing measured which offer converts.

    utm_source/medium/campaign are the shape GHL and GA already read.
    `lead_id` and `offer` are ours, kept as separate params rather than folded
    into the utm_ ones so the funnel can key on them without parsing a label.

    Params, not a per-offer URL: the destination is deliberately the same page
    for all three offers, and splitting it would mean three funnels to keep in
    step. Query strings survive the 200 on /letschat; confirm they survive GHL's
    own form handoff before anything downstream is built on them.
    """
    params = f"utm_source=lead_magnet&utm_medium={offer}&utm_campaign=tsd_outreach&offer={offer}"
    if prospect_id:
        params += f"&lead_id={prospect_id}"
    return f"{BOOKING_URL}?{params}"


# GitHub Pages base URL for deployed previews
GITHUB_USER = "joshclifford"
GITHUB_REPO = "lvrg-previews"
PREVIEW_BASE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}"

# Output dirs
import os
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
SITES_DIR = os.path.join(ENGINE_DIR, "output", "sites")
EMAILS_DIR = os.path.join(ENGINE_DIR, "output", "emails")
INTEL_DIR = os.path.join(ENGINE_DIR, "output", "intel")

os.makedirs(SITES_DIR, exist_ok=True)
os.makedirs(EMAILS_DIR, exist_ok=True)
os.makedirs(INTEL_DIR, exist_ok=True)
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
