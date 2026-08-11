"""
LVRG Lead Magnet Engine V2 — Prospect Intel Gatherer
Scrapes the prospect's own site via Firecrawl, extracts structured intel with
Claude, and pulls real photos off their page.

Photos used to come from scraping Yelp's HTML. Yelp returns 403 to datacenter
IPs, the failure was silent, and the generator quietly fell back to "gradients
only" — which is why V2 output looked identical to V1. Their own site is a
better source anyway: real photos of that business, no third party to be
blocked by, and nothing to misattribute.
"""

import requests
import json
import os
import time
import re
import anthropic
from html import unescape as _unescape
from urllib.parse import urljoin
from config import INTEL_DIR

def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    return anthropic.Anthropic(api_key=key)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v2/scrape"
FIRECRAWL_SEARCH = "https://api.firecrawl.dev/v2/search"

# Filenames/paths that are almost never a photo of the business.
_JUNK_IMAGE = re.compile(
    r"(logo|icon|favicon|sprite|badge|avatar|placeholder|pixel|tracking|"
    r"spinner|loader|arrow|chevron|bullet|divider|pattern|1x1|blank)",
    re.I,
)
_PHOTO_EXT = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)

# Campaign artwork. Real on the page, wrong on a rebuilt site — a June giveaway
# banner as the hero in August reads as stale.
_PROMO_IMAGE = re.compile(
    r"(giveaway|promo|coupon|discount|webinar|ebook|tipsheet|whitepaper|"
    r"popup|pop-up|banner|cta-|-cta|newsletter|signup)",
    re.I,
)

# WordPress writes crops as name-1024x839.png next to the full-size name.png.
_WP_THUMB = re.compile(r"-(\d{2,4})x(\d{2,4})(\.(?:jpe?g|png|webp))$", re.I)


def _photo_score(url: str) -> int:
    """Rank candidates so the hero is a photo, not a promo banner.

    Ordering used to be document order, which put AccuLynx's "GIVEAWAY" graphic
    ahead of their product screenshot.
    """
    bare = url.split("?")[0]
    score = 0

    # Photographs are overwhelmingly JPEG/WebP. Transparent PNGs are logos,
    # cut-outs and composites — they render badly behind object-fit: cover.
    if re.search(r"\.(jpe?g|webp)$", bare, re.I):
        score += 3

    if _PROMO_IMAGE.search(bare):
        score -= 5

    m = _WP_THUMB.search(bare)
    if m:
        width = int(m.group(1))
        score += 3 if width >= 800 else 1 if width >= 400 else -2
    else:
        score += 2  # no crop suffix — likely already the original

    return score


# A crop at least this wide is already fine for a hero — upgrading it just
# makes the page heavier. Only genuinely small crops are worth replacing.
_UPGRADE_BELOW_WIDTH = 800
# Refuse originals big enough to hurt page load. AccuLynx's full-size homepage
# graphics are ~1.5 MB each; four of those is a 6 MB page.
_MAX_UPGRADE_BYTES = 900_000


def _full_size(url: str) -> str:
    """Trade a small WordPress crop for the original, when that's an improvement.

    Two guards, both learned from real pages: a guessed original may not exist
    (publishing a broken image is worse than a soft one), and the original may
    be far too heavy to put on a page.
    """
    bare, _, query = url.partition("?")
    m = _WP_THUMB.search(bare)
    if not m or int(m.group(1)) >= _UPGRADE_BELOW_WIDTH:
        return url

    candidate = _WP_THUMB.sub(r"\3", bare)
    try:
        resp = requests.head(candidate, headers=HEADERS, timeout=4, allow_redirects=True)
        if resp.status_code != 200:
            return url
        if not resp.headers.get("Content-Type", "").startswith("image/"):
            return url
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > _MAX_UPGRADE_BYTES:
            return url
        return candidate + (("?" + query) if query else "")
    except Exception:
        return url


def fetch_site_content(domain: str) -> dict:
    """Scrape the prospect's site. Returns {"text": str, "html": str}.

    Firecrawl first — it renders JS and returns clean markdown, and it is not
    truncated (V1 cut at 4,000 chars, so only half a homepage reached the model).
    Falls back to a direct fetch so a Firecrawl outage degrades instead of
    failing the build outright.
    """
    url = f"https://{domain}" if not domain.startswith("http") else domain

    if FIRECRAWL_KEY:
        try:
            resp = requests.post(
                FIRECRAWL_SCRAPE,
                headers={"Authorization": f"Bearer {FIRECRAWL_KEY}",
                         "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown", "html"], "onlyMainContent": False},
                # Sized to the caller's 135s budget, not to Firecrawl's patience.
                timeout=25,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}) or {}
                text = (data.get("markdown") or "").strip()
                html = data.get("html") or ""
                if text:
                    print(f"  [intel] Firecrawl scrape OK — {len(text)} chars")
                    return {"text": text, "html": html}
                print("  [intel] Firecrawl returned no markdown — falling back")
            else:
                print(f"  [intel] Firecrawl scrape failed: {resp.status_code} — falling back")
        except Exception as e:
            print(f"  [intel] Firecrawl scrape error: {e} — falling back")
    else:
        print("  [intel] FIRECRAWL_API_KEY not set — using direct fetch")

    # Fallback: direct fetch, tags stripped. No 4,000-char cap here either.
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        html = resp.text
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        print(f"  [intel] Direct fetch OK — {len(text)} chars")
        return {"text": text, "html": html}
    except Exception as e:
        print(f"  [intel] Fetch failed: {e}")
        return {"text": "", "html": ""}


def extract_photos(html: str, base_url: str, limit: int = 6) -> list:
    """Pull real photos of the business off their own page.

    Prefers the og:image (almost always the hero shot), then <img> sources,
    skipping the logos, icons and tracking pixels that make up most <img> tags
    on a small business site.
    """
    if not html:
        return []

    candidates = []

    og = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        html, re.I,
    )
    if og:
        candidates.append(og.group(1))

    # src, and the first URL of any srcset (largest is usually last, but the
    # first is always well-formed — good enough for a hero candidate).
    candidates += re.findall(r'<img[^>]+src=["\']([^"\']+)', html, re.I)
    candidates += [s.split()[0] for s in
                   re.findall(r'<img[^>]+srcset=["\']([^"\',]+)', html, re.I) if s.strip()]

    seen, kept = set(), []
    for position, raw in enumerate(candidates):
        # Unescape first: HTML-encoded query strings (?v=1&amp;width=700) are
        # common on Shopify/WordPress and produce a dead URL if left as-is.
        src = _unescape(raw.strip())
        if not src or src.startswith("data:"):
            continue
        absolute = urljoin(base_url, src)
        if not absolute.startswith(("http://", "https://")):
            continue
        if not _PHOTO_EXT.search(absolute):
            continue          # skips .svg, .gif and extensionless endpoints
        if _JUNK_IMAGE.search(absolute):
            continue
        key = absolute.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        kept.append((absolute, position))

    # Rank before truncating. Document order alone puts whatever the site
    # happens to render first — often a promo banner — into the hero slot.
    # Position stays as the tie-breaker so equal-quality images keep page order.
    kept.sort(key=lambda pair: (-_photo_score(pair[0]), pair[1]))

    return [_full_size(url) for url, _ in kept[:limit]]


def _mentions_business(sentence: str, business_name: str) -> bool:
    """Does this sentence actually name the business?

    The old check matched on the FIRST WORD of the name, so "The Little Door"
    matched any sentence containing "the". Require the full name, or a
    distinctive word from it (4+ chars, not a stopword).
    """
    s = sentence.lower()
    name = business_name.lower().strip()
    if name and name in s:
        return True
    stop = {"the", "and", "for", "cafe", "shop", "inc", "llc", "co", "company", "group"}
    tokens = [w for w in re.findall(r"[a-z0-9]+", name) if len(w) >= 4 and w not in stop]
    return any(t in s for t in tokens)


def fetch_press_mentions(business_name: str, location: str = "") -> list:
    """Search for press/media mentions via Firecrawl search.

    Was pinned to /v1/search (deprecated, returns 500 for a valid key) and to
    a hardcoded San Diego + San Diego-only publication list, which was wrong
    for every prospect outside that city.
    """
    if not FIRECRAWL_KEY:
        print("  [intel] FIRECRAWL_API_KEY not set — skipping press search")
        return []

    try:
        where = f" {location}" if location else ""
        query = f'"{business_name}"{where} review OR feature OR profile'

        resp = requests.post(
            FIRECRAWL_SEARCH,
            headers={"Authorization": f"Bearer {FIRECRAWL_KEY}",
                     "Content-Type": "application/json"},
            json={
                "query": query,
                "limit": 5,
                "country": "US",
                "ignoreInvalidURLs": True,
                "scrapeOptions": {"formats": ["markdown"]},
            },
            timeout=15,  # press is a nice-to-have; never let it eat the build budget
        )

        if resp.status_code != 200:
            print(f"  [intel] Press search failed: {resp.status_code}")
            return []

        payload = resp.json().get("data", {})
        # v2 nests results under data.web; v1 returned a bare list.
        results = payload.get("web", []) if isinstance(payload, dict) else payload

        own_domain = business_name.lower().replace(" ", "")
        mentions = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            if not (title and url):
                continue
            source = re.sub(r'https?://(www\.)?', '', url).split('/')[0]
            if own_domain and own_domain in source.replace("-", "").replace(".", ""):
                continue  # their own site isn't press coverage

            content = r.get("markdown") or r.get("description") or ""
            sentences = re.split(r'(?<=[.!?])\s+', content)
            relevant = [s.strip() for s in sentences
                        if len(s) > 30 and _mentions_business(s, business_name)]
            quote = relevant[0] if relevant else ""

            mentions.append({"source": source, "title": title,
                             "url": url, "quote": quote[:200]})

        print(f"  [intel] Press: {len(mentions)} mentions found")
        return mentions

    except Exception as e:
        print(f"  [intel] Press fetch failed: {e}")
        return []


def extract_intel_with_claude(domain: str, raw_text: str) -> dict:
    """Use Claude to extract structured intel from raw site content."""
    
    prompt = f"""Analyze this website content from {domain} and extract structured information.

WEBSITE CONTENT:
{raw_text}

Extract and return a JSON object with these fields:
- business_name: The name of the business (string)
- tagline: Their tagline or hero headline (string, empty if none)
- description: What the business does in 2-3 sentences (string)
- services: List of main services/offerings (array of strings)
- location: City, neighborhood, or address (string)
- phone: Phone number if present (string, empty if none)
- email: Email address if present (string, empty if none)
- hours: Business hours if present (string, empty if none)
- social_proof: Awards, years in business, testimonials, notable claims (string)
- key_cta: Their main call to action if any (string, empty if none)
- missing: Important elements missing from the site - be specific (string, e.g. "no chat widget, no online booking, no menu listed")
- brand_vibe: Describe the brand feel in 5-10 words (string, e.g. "dark moody speakeasy with gold accents")
- primary_color: Best guess at primary brand color as hex (string, e.g. "#1a1a2e")
- secondary_color: Secondary brand color as hex (string)
- business_type: One of: restaurant, bar, catering, coffee_shop, retail, craft_beverage, service, other (string)
- pain_point: The single biggest conversion problem with their current site in one sentence (string)
- chat_persona: How an AI chat agent should behave for this business in one sentence (string)
- cta_angle: The best CTA angle for this business - what they most want customers to do (string, e.g. "Book a Private Event", "Get a Free Quote", "Reserve a Table")
- owner_name: Owner or decision maker first name if mentioned anywhere on the site (string, empty if not found)
- neighborhood: Specific San Diego neighborhood or area (string, e.g. "North Park", "Little Italy", "Gaslamp", empty if unknown)

Return ONLY valid JSON, no markdown, no explanation."""

    client = _get_client()
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.split("```")[0]
    
    try:
        return json.loads(raw)
    except:
        return {}


# The intel stage's share of the caller's 135s build budget. Site generation
# needs ~82-95s of that and cannot be shortened, so once intel has spent this
# long the optional enrichment is skipped rather than pushing the whole build
# past the deadline. A page with no press quotes beats a build that times out.
INTEL_BUDGET_SECONDS = int(os.environ.get("INTEL_BUDGET_SECONDS", "30"))


def scrape_site(domain: str) -> dict:
    """Full intel gather for a prospect domain."""

    started = time.monotonic()
    domain = domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    url = f"https://{domain}"
    print(f"  [intel] Fetching {url}...")
    
    scraped = fetch_site_content(domain)
    raw_text, raw_html = scraped["text"], scraped["html"]

    # Never build from an unread site. Without this the model invents the whole
    # business from the domain string and we publish a fiction under a real
    # company's name — see familydentalcare.com, 2026-08-04.
    if not raw_text:
        raise ValueError(
            f"Could not read {url} — unreachable or returned nothing. "
            f"Refusing to build a site for a business we never read."
        )

    print(f"  [intel] Extracting structured intel with Claude...")
    extracted = extract_intel_with_claude(domain, raw_text)

    business_name = extracted.get("business_name") or domain.split(".")[0].replace("-", " ").title()
    location = extracted.get("location", "")

    # Photos come from the prospect's own page now. Yelp 403s datacenter IPs,
    # so the old path returned nothing on every single build.
    photos = extract_photos(raw_html, url)
    print(f"  [intel] Photos: {len(photos)} found on their site")

    # Press is the one genuinely optional stage — skip it if the scrape and the
    # extraction already ate the intel budget.
    elapsed = time.monotonic() - started
    if elapsed > INTEL_BUDGET_SECONDS:
        print(f"  [intel] Skipping press search — intel already took {elapsed:.0f}s "
              f"of its {INTEL_BUDGET_SECONDS}s budget")
        press = []
    else:
        print(f"  [intel] Searching for press mentions...")
        press = fetch_press_mentions(business_name, location)

    intel = {
        "domain": domain,
        "url": url,
        "business_name": business_name,
        "tagline": extracted.get("tagline", ""),
        "description": extracted.get("description", f"Local business at {domain}"),
        "services": extracted.get("services", []),
        "location": location,
        "phone": extracted.get("phone", ""),
        "email": extracted.get("email", ""),
        "hours": extracted.get("hours", ""),
        "social_proof": extracted.get("social_proof", ""),
        "key_cta": extracted.get("key_cta", ""),
        "missing": extracted.get("missing", "chat widget, clear CTA, contact info"),
        "brand_vibe": extracted.get("brand_vibe", "clean, modern local business"),
        "primary_color": extracted.get("primary_color", "#1a1a2e"),
        "secondary_color": extracted.get("secondary_color", "#c9a961"),
        "business_type": extracted.get("business_type", "other"),
        "pain_point": extracted.get("pain_point", "Visitors can't easily take action on the site"),
        "chat_persona": extracted.get("chat_persona", "Friendly assistant that answers questions and helps customers"),
        "cta_angle": extracted.get("cta_angle", "Get in Touch"),
        "owner_name": extracted.get("owner_name", ""),
        "neighborhood": extracted.get("neighborhood", ""),
        # Sample of the page passed to the generator prompt. The full text
        # goes to the intel extraction above; this is the slice the site
        # writer sees for voice and detail. Was 1000 when the scrape itself
        # was capped at 4000 — there is room for more now.
        "raw_text": raw_text[:6000],
        # V2 enrichment
        "photos": photos,
        "press_mentions": press,
        # Rating and reviews are real data the app already holds (Google Maps via
        # Apify). api.py merges them in from engine_queue when present. We no
        # longer scrape them — the old Yelp regex matched any JSON field named
        # "text" and handed arbitrary strings to the model as verbatim customer
        # testimonials, which is worse than inventing one.
        "rating": None,
        "review_count": None,
        "reviews": [],
    }
    
    print(f"  [intel] ✓ {intel['business_name']} — {intel['business_type']} — {intel['location']}")
    
    os.makedirs(INTEL_DIR, exist_ok=True)
    slug = domain.replace(".", "_")
    with open(os.path.join(INTEL_DIR, f"{slug}.json"), "w") as f:
        json.dump(intel, f, indent=2)
    
    return intel


def grade_site(intel: dict) -> dict:
    """Score the site 0-10 against the LVRG rubric. Target: 2-7."""
    
    scores = {}
    scores["value_prop"] = 7 if intel.get("tagline") else (5 if len(intel.get("description","")) > 30 else 2)
    cta = (intel.get("key_cta") or "").lower()
    scores["primary_cta"] = 8 if any(w in cta for w in ["book","order","call","get","contact","buy","reserve","quote"]) else (4 if cta else 1)
    contact_score = 0
    if intel.get("phone"): contact_score += 4
    if intel.get("email"): contact_score += 3
    if intel.get("location") and intel["location"] != "San Diego, CA": contact_score += 3
    scores["contact"] = min(contact_score, 10)
    sp = intel.get("social_proof", "")
    scores["social_proof"] = 8 if len(sp) > 50 else (5 if len(sp) > 10 else 2)
    scores["hours"] = 6 if intel.get("hours") else 2
    missing = (intel.get("missing") or "").lower()
    has_chat = "chat" not in missing
    scores["chat"] = 8 if has_chat else 0
    gap_count = sum(1 for w in ["chat","booking","menu","email","phone","contact"] if w in missing)
    scores["gaps"] = max(0, 10 - gap_count * 2)
    total = round(sum(scores.values()) / len(scores))
    return {"scores": scores, "total": total, "verdict": get_verdict(total), "worth_targeting": 2 <= total <= 7}


def get_verdict(score: int) -> str:
    if score <= 2: return "Barely functional — may not convert well"
    if score <= 4: return "Weak — strong opportunity"
    if score <= 6: return "Mid — clear conversion gaps"
    if score <= 8: return "Good — may not need us"
    return "Strong — not a target"
