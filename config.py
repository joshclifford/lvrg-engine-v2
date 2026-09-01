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
