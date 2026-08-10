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
BOOKING_URL = "https://theresandiego.com/advertise/"

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
