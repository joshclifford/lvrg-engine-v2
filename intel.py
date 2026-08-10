"""
LVRG Lead Magnet Engine V2 — Prospect Intel Gatherer
Fetches site content via requests + Claude extraction.
V2 additions: Yelp photo URLs, press/media mentions via Firecrawl search.
"""

import requests
import json
import os
import re
import anthropic
from config import INTEL_DIR

def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    return anthropic.Anthropic(api_key=key)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY", "")


def fetch_site_content(domain: str) -> str:
    """Fetch raw HTML/text from a site."""
    url = f"https://{domain}" if not domain.startswith("http") else domain
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        text = resp.text
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:4000]
    except Exception as e:
        print(f"  [intel] Fetch failed: {e}")
        return ""


def fetch_yelp_data(business_name: str, location: str) -> dict:
    """Fetch Yelp rating, review count, top reviews, and photo URLs."""
    try:
        # Search Yelp for the business
        search_term = f"{business_name} {location}".replace(" ", "+")
        yelp_url = f"https://www.yelp.com/search?find_desc={search_term}&find_loc=San+Diego%2C+CA"
        
        resp = requests.get(yelp_url, headers=HEADERS, timeout=15)
        html = resp.text
        
        # Extract JSON data embedded in Yelp's page
        match = re.search(r'"businessesFound":\{"businesses":\[(\{.+?\})\]', html)
        
        # Try to find photo CDN URLs — Yelp uses yelpcdn.com
        photo_urls = re.findall(r'https://s3-media\d*\.fl\.yelpcdn\.com/bphoto/[^"\']+(?:o\.jpg|l\.jpg|ms\.jpg|348s\.jpg)', html)
        # Deduplicate and grab originals (o.jpg) preferably
        seen = set()
        clean_photos = []
        for p in photo_urls:
            # Normalize to original size
            base = re.sub(r'/(o|l|ms|348s|ls|ls6|300s|250s|60s)\.jpg$', '/o.jpg', p)
            if base not in seen:
                seen.add(base)
                clean_photos.append(base)
        
        # Extract rating
        rating_match = re.search(r'"rating":(\d+\.?\d*)', html)
        rating = float(rating_match.group(1)) if rating_match else None
        
        # Extract review count
        review_match = re.search(r'"reviewCount":(\d+)', html)
        review_count = int(review_match.group(1)) if review_match else None
        
        # Extract top review snippets
        review_snippets = re.findall(r'"text":"([^"]{40,200})"', html)
        top_reviews = list(dict.fromkeys(review_snippets))[:5]  # dedupe, keep 5
        
        result = {
            "rating": rating,
            "review_count": review_count,
            "top_reviews": top_reviews,
            "photo_urls": clean_photos[:6],  # top 6 photos
        }
        
        print(f"  [intel] Yelp: {rating}★ ({review_count} reviews), {len(clean_photos)} photos, {len(top_reviews)} review snippets")
        return result
        
    except Exception as e:
        print(f"  [intel] Yelp fetch failed: {e}")
        return {}


def fetch_press_mentions(business_name: str) -> list:
    """Search for press/media mentions via Firecrawl search."""
    try:
        query = f'"{business_name}" San Diego site:eater.com OR site:sandiegomagazine.com OR site:sdcitybeat.com OR site:timeout.com OR site:thrillist.com OR site:zagat.com OR site:infatuation.com'
        
        resp = requests.post(
            "https://api.firecrawl.dev/v1/search",
            headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"},
            json={"query": query, "limit": 5, "scrapeOptions": {"formats": ["markdown"]}},
            timeout=20
        )
        
        if resp.status_code != 200:
            print(f"  [intel] Press search failed: {resp.status_code}")
            return []
        
        results = resp.json().get("data", [])
        mentions = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            # Try to pull a quote from the markdown content
            content = r.get("markdown", r.get("description", ""))
            # Find a sentence mentioning the business
            sentences = re.split(r'(?<=[.!?])\s+', content)
            relevant = [s.strip() for s in sentences if business_name.lower().split()[0].lower() in s.lower() and len(s) > 30]
            quote = relevant[0] if relevant else ""
            
            if title and url:
                source = re.sub(r'https?://(www\.)?', '', url).split('/')[0]
                mentions.append({"source": source, "title": title, "url": url, "quote": quote[:200]})
        
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


def scrape_site(domain: str) -> dict:
    """Full intel gather for a prospect domain."""
    
    domain = domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    url = f"https://{domain}"
    print(f"  [intel] Fetching {url}...")
    
    raw_text = fetch_site_content(domain)
    
    if raw_text:
        print(f"  [intel] Extracting structured intel with Claude...")
        extracted = extract_intel_with_claude(domain, raw_text)
    else:
        extracted = {}
    
    business_name = extracted.get("business_name") or domain.split(".")[0].replace("-", " ").title()
    location = extracted.get("location", "San Diego, CA")

    # V2: Fetch Yelp data (photos + reviews)
    print(f"  [intel] Fetching Yelp data...")
    yelp = fetch_yelp_data(business_name, location)

    # V2: Fetch press mentions
    print(f"  [intel] Searching for press mentions...")
    press = fetch_press_mentions(business_name)

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
        "raw_text": raw_text[:1000],
        # V2 enrichment
        "yelp_rating": yelp.get("rating"),
        "yelp_review_count": yelp.get("review_count"),
        "yelp_reviews": yelp.get("top_reviews", []),
        "yelp_photos": yelp.get("photo_urls", []),
        "press_mentions": press,
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
