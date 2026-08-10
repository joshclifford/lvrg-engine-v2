"""
LVRG Lead Magnet Engine V2 — Site + Email Generator
V2 improvements:
  - Tailwind CDN (no inline-style constraint)
  - Real Yelp photos passed as <img> URLs
  - Real Yelp reviews as testimonials
  - Press/media mentions pulled in
  - Business-type design personalities
  - Better headline direction
"""

import os
import re
import json
import anthropic

from config import SITES_DIR, BOOKING_URL, SENDER_NAME, SENDER_AGENCY

def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    return anthropic.Anthropic(api_key=key)


# ── Design personality by business type ───────────────────────────────────────
DESIGN_PERSONALITIES = {
    "bar": {
        "mood": "Dark, moody, atmospheric. Think: low lighting, leather, aged wood. Rich blacks and deep tones. Gold or amber accents only.",
        "fonts": "Playfair Display for headings (editorial weight), Inter for body. Large, dramatic type.",
        "layout": "Full-bleed hero with overlay text. Sections with dark backgrounds. Menu items styled like a printed cocktail menu.",
        "hero_style": "Photography-first. Large image, minimal overlay text. The photo does the heavy lifting.",
        "references": "Think Nobu, Death & Co, Employees Only aesthetic — upscale but not stuffy.",
    },
    "restaurant": {
        "mood": "Warm, inviting, appetite-driving. Rich textures. Food photography hero. Feels like a reservation is worth making.",
        "fonts": "Cormorant Garamond or Playfair Display for headings. Clean sans-serif for body.",
        "layout": "Large hero photo. Story section. Menu preview. Reservation CTA prominently placed.",
        "hero_style": "Full-bleed food/ambiance photo. Headline centered with subtle text-shadow.",
        "references": "Think Bestia, Nobu, or a well-designed farm-to-table spot — editorial photography, generous whitespace.",
    },
    "coffee_shop": {
        "mood": "Warm and handcrafted. Cream, warm whites, deep browns. Feels like a slow Saturday morning.",
        "fonts": "Recoleta or DM Serif Display for headings. Nunito or DM Sans for body.",
        "layout": "Cozy grid layouts. Feature items front and center. Community feel.",
        "hero_style": "Intimate close-up photography. Warm color grade. Headline is conversational, not corporate.",
        "references": "Think Blue Bottle, Intelligentsia, or a beloved neighborhood cafe — approachable luxury.",
    },
    "catering": {
        "mood": "Professional, confident, event-ready. Clean and modern but with warmth. Makes you trust them with your biggest day.",
        "fonts": "Montserrat or Raleway for headings. Source Sans Pro for body. Elegant but not fussy.",
        "layout": "Services/packages clear and scannable. Gallery of events. Testimonials prominent. CTA is 'Get a Quote'.",
        "hero_style": "Event photography — beautifully set tables, people enjoying themselves. Trust signals early.",
        "references": "Think high-end catering brands — clean, portfolio-driven, quote-focused.",
    },
    "craft_beverage": {
        "mood": "Artisan, passionate, story-driven. Feels like the founders are obsessed with their craft.",
        "fonts": "Space Grotesk or Syne for headings. Clean mono accents for details. Bold weight.",
        "layout": "Brand story front and center. Product cards. Distribution/where-to-find section.",
        "hero_style": "Product + lifestyle. The product is the hero. Headline speaks to craft and obsession.",
        "references": "Think craft brewery / roastery landing pages — passionate, textured, brand-forward.",
    },
    "retail": {
        "mood": "Clean, product-forward, conversion-optimized. The product should be impossible to ignore.",
        "fonts": "Inter or Plus Jakarta Sans. Clean, neutral, product-first.",
        "layout": "Product grid. Social proof bar. CTA early and often. Clean navigation.",
        "hero_style": "Product photography on clean background. Headline is benefit-driven.",
        "references": "Think clean DTC brand pages — Allbirds, Glossier, Warby Parker aesthetic.",
    },
    "other": {
        "mood": "Clean, modern, professional. Clear value prop. Trustworthy.",
        "fonts": "Inter for body, Sora or DM Sans for headings. Neutral and versatile.",
        "layout": "Hero with clear value prop. Services. Social proof. CTA.",
        "hero_style": "Bold headline with supporting subhead. Clean gradient or photo background.",
        "references": "Clean modern agency/service brand aesthetic.",
    },
}

def _get_design_personality(business_type: str) -> dict:
    return DESIGN_PERSONALITIES.get(business_type, DESIGN_PERSONALITIES["other"])


def _build_chat_widget(intel: dict) -> str:
    """Build the chat widget HTML — injected programmatically after Claude generates the site."""
    business_name = intel.get("business_name", "this business")
    persona = intel.get("chat_persona", f"Friendly assistant for {business_name}")
    primary_color = intel.get("primary_color", "#f59e0b")
    
    return f"""
<!-- LVRG AI Chat Widget -->
<style>
  #lvrg-chat-btn {{
    position: fixed; bottom: 24px; right: 24px;
    width: 56px; height: 56px; border-radius: 50%;
    background: {primary_color}; color: #000;
    border: none; cursor: pointer; z-index: 2147483647;
    font-size: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    display: flex; align-items: center; justify-content: center;
    transition: transform 0.2s;
  }}
  #lvrg-chat-btn:hover {{ transform: scale(1.1); }}
  #lvrg-chat-panel {{
    position: fixed; bottom: 92px; right: 24px;
    width: 340px; max-height: 480px;
    background: #18181b; border: 1px solid #3f3f46;
    border-radius: 16px; z-index: 2147483647;
    display: none; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    overflow: hidden;
  }}
  #lvrg-chat-panel.open {{ display: flex; }}
  #lvrg-chat-header {{
    padding: 14px 16px; background: {primary_color};
    font-weight: 700; font-size: 13px; color: #000;
  }}
  #lvrg-chat-messages {{
    flex: 1; overflow-y: auto; padding: 12px;
    display: flex; flex-direction: column; gap: 8px;
  }}
  .lvrg-msg {{
    max-width: 85%; padding: 8px 12px; border-radius: 12px;
    font-size: 13px; line-height: 1.4;
  }}
  .lvrg-msg.bot {{
    background: #27272a; color: #e4e4e7; align-self: flex-start;
    border-bottom-left-radius: 4px;
  }}
  .lvrg-msg.user {{
    background: {primary_color}; color: #000; align-self: flex-end;
    border-bottom-right-radius: 4px;
  }}
  #lvrg-chat-input-row {{
    display: flex; padding: 8px; border-top: 1px solid #3f3f46; gap: 6px;
  }}
  #lvrg-chat-input {{
    flex: 1; background: #27272a; border: 1px solid #3f3f46;
    border-radius: 8px; padding: 8px 10px; font-size: 13px;
    color: #fff; outline: none;
  }}
  #lvrg-chat-send {{
    background: {primary_color}; border: none; border-radius: 8px;
    padding: 8px 12px; cursor: pointer; font-size: 13px; font-weight: 700; color: #000;
  }}
</style>

<button id="lvrg-chat-btn" onclick="document.getElementById('lvrg-chat-panel').classList.toggle('open')">💬</button>

<div id="lvrg-chat-panel">
  <div id="lvrg-chat-header">Chat with {business_name}</div>
  <div id="lvrg-chat-messages">
    <div class="lvrg-msg bot">Hey! I'm the AI assistant for {business_name}. How can I help you today?</div>
  </div>
  <div id="lvrg-chat-input-row">
    <input id="lvrg-chat-input" placeholder="Ask anything..." onkeydown="if(event.key==='Enter')lvrgSend()"/>
    <button id="lvrg-chat-send" onclick="lvrgSend()">→</button>
  </div>
</div>

<script>
const LVRG_PERSONA = "{persona.replace('"', "'")}";
const LVRG_BIZ = "{business_name.replace('"', "'")}";
const LVRG_BOOKING = "{BOOKING_URL}";
let lvrgHistory = [];

async function lvrgSend() {{
  const inp = document.getElementById('lvrg-chat-input');
  const msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  lvrgAddMsg(msg, 'user');
  lvrgHistory.push({{"role":"user","content":msg}});
  const resp = await fetch('https://lvrg-engine-production.up.railway.app/chat', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      messages: lvrgHistory,
      persona: LVRG_PERSONA,
      business_name: LVRG_BIZ,
      booking_url: LVRG_BOOKING
    }})
  }});
  const data = await resp.json();
  const reply = data.reply || "Let me connect you with our team!";
  lvrgHistory.push({{"role":"assistant","content":reply}});
  lvrgAddMsg(reply, 'bot');
}}

function lvrgAddMsg(text, role) {{
  const msgs = document.getElementById('lvrg-chat-messages');
  const div = document.createElement('div');
  div.className = 'lvrg-msg ' + role;
  div.textContent = text;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}}
</script>
"""


def generate_site(intel: dict, prospect_id: str, notes: str = "") -> str:
    """Generate a complete single-file HTML site for a prospect. Returns folder path."""
    
    print(f"  [generator] Generating V2 site for {intel['business_name']}...")
    
    notes_block = f"\n\nSPECIAL INSTRUCTIONS:\n{notes}\n" if notes else ""
    design = _get_design_personality(intel.get("business_type", "other"))
    
    # Build photo block — only use real Yelp photos, skip if none
    yelp_photos = intel.get("yelp_photos", [])
    if yelp_photos:
        photo_block = f"""REAL PHOTOS (use these as actual <img> tags — link directly to the URLs):
{chr(10).join(f'  {i+1}. {url}' for i, url in enumerate(yelp_photos[:4]))}
Use the best photo as the hero background (as an <img> with object-fit:cover, or as a CSS background-image url()).
Use others in gallery/services sections where they fit naturally.
Only use a photo if it makes sense in context — don't force it."""
    else:
        photo_block = "PHOTOS: No real photos available. Use CSS gradients and brand colors only — no placeholder images."
    
    # Build reviews block — real Yelp reviews only
    yelp_reviews = intel.get("yelp_reviews", [])
    yelp_rating = intel.get("yelp_rating")
    yelp_count = intel.get("yelp_review_count")
    if yelp_reviews:
        reviews_block = f"""REAL CUSTOMER REVIEWS (use these verbatim as testimonials — do not make up quotes):
Rating: {yelp_rating}★ ({yelp_count} reviews on Yelp)
{chr(10).join(f'  "{r}"' for r in yelp_reviews[:3])}"""
    else:
        reviews_block = f"REVIEWS: No Yelp reviews available. Use the rating {yelp_rating}★ ({yelp_count} reviews) as a social proof stat if available, but do NOT write fake testimonial quotes."

    # Build press block
    press_mentions = intel.get("press_mentions", [])
    if press_mentions:
        press_block = f"""PRESS MENTIONS (use these as credibility signals — quote them if there's a good quote):
{chr(10).join(f'  - {p["source"]}: "{p["title"]}"' + (f' — "{p["quote"]}"' if p.get("quote") else '') for p in press_mentions[:3])}"""
    else:
        press_block = "PRESS: No press mentions found."

    site_prompt = f"""You are building a high-end preview website for {intel['business_name']}.{notes_block}

━━━ BUSINESS INTEL ━━━
- Business: {intel['business_name']}
- Type: {intel.get('business_type', 'other')}
- Domain: {intel['domain']}
- Description: {intel['description']}
- Services: {', '.join(intel['services']) if intel['services'] else 'Not listed'}
- Location: {intel['location']}
- Neighborhood: {intel.get('neighborhood', '')}
- Phone: {intel.get('phone', 'Not listed')}
- Hours: {intel.get('hours', 'Not listed')}
- Brand vibe: {intel.get('brand_vibe', 'clean, modern')}
- Primary color: {intel.get('primary_color', '#333')}
- Pain point: {intel.get('pain_point', '')}
- CTA: {intel.get('cta_angle', 'Get in Touch')}

━━━ REAL CONTENT ━━━
{photo_block}

{reviews_block}

{press_block}

━━━ DESIGN PERSONALITY ━━━
Mood: {design['mood']}
Fonts: {design['fonts']}
Layout approach: {design['layout']}
Hero style: {design['hero_style']}
Reference aesthetic: {design['references']}

━━━ TECH STACK ━━━
Use Tailwind CSS via CDN — include this in <head>:
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: '{intel.get('primary_color','#f59e0b')}' }} }} }} }}</script>
Use Google Fonts matching the design personality above.
This gives you full Tailwind (hover:, focus:, responsive, animations) — use it fully.
NO inline style= attributes. Use Tailwind classes exclusively.

━━━ STRUCTURE ━━━
Build a single-file HTML homepage (index.html).

1. CLAIM BAR — sticky, black bg, centered single line:
   "This site was built for **{intel['business_name']}** by LVRG Agency"
   + gold pill "Claim This Site →" button linking to {BOOKING_URL}
   Everything centered on one line. No left/right split.

2. NAV — business name as logo, 3-4 links, primary CTA button

3. HERO — follow the hero style for this business type above.
   Headline rule: Make it UNEXPECTED and SPECIFIC. Own something.
   Bad: "San Diego's Premier Cocktail Experience"
   Good: "The bar North Park didn't know it needed — until now"
   Good: "Every Sunday feels different here"
   Good: "Where the regulars send their friends"
   One bold headline + one supporting line. Two CTAs.

4. SOCIAL PROOF BAR — use REAL stats: Yelp rating, review count, years open, awards

5. SERVICES/MENU — 3 feature cards using their REAL services

6. TESTIMONIALS — ONLY use real Yelp quotes provided above.
   If none available, use a social proof stat block instead (no fake quotes).

7. PRESS / AS SEEN IN — if press mentions provided, show source logos as text badges.
   Skip this section entirely if no press.

8. CTA BANNER — headline + copy driving toward: {intel.get('cta_angle', 'booking')}

9. FOOTER — {intel.get('location','')}, {intel.get('phone','')}, hours

━━━ COPY RULES ━━━
- Reference {intel.get('neighborhood') or intel.get('location','').split(',')[0]} naturally in copy
- Every CTA drives toward: {intel.get('cta_angle', 'booking')}
- Address this pain point: {intel.get('pain_point', '')}
- NEVER write fake testimonials — if no real reviews, skip quotes entirely

━━━ OUTPUT ━━━
Return ONLY the complete HTML. No explanation. No markdown fences. No chat widget (injected separately).
Start with <!DOCTYPE html>"""

    client = _get_client()
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=14000,
        messages=[{"role": "user", "content": site_prompt}]
    )
    
    html = response.content[0].text.strip()
    
    # Strip markdown fences if Claude wrapped it
    if html.startswith("```"):
        html = re.sub(r'^```[a-z]*\n?', '', html)
        html = re.sub(r'\n?```$', '', html)

    # Close a truncated response before anything else touches it. Without this
    # the widget injection below staples itself onto an unclosed document and we
    # publish malformed HTML. Parity with v1 generator.py.
    if not html.rstrip().endswith("</html>"):
        if "</body>" not in html:
            html += "\n</body>"
        html += "\n</html>"

    # Inject chat widget before </body> — now always present, so the else is
    # only a belt-and-braces fallback.
    widget_html = _build_chat_widget(intel)
    if "</body>" in html:
        html = html.replace("</body>", widget_html + "\n</body>")
    else:
        html += widget_html
    
    # Save
    site_dir = os.path.join(SITES_DIR, prospect_id)
    os.makedirs(site_dir, exist_ok=True)
    index_path = os.path.join(site_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"  [generator] ✓ V2 site saved to {site_dir}")
    return site_dir


def generate_email(intel: dict, grade: dict, prospect_id: str) -> dict:
    """Generate cold outreach email. grade and prospect_id kept for api.py compatibility."""
    preview_url = f"https://joshclifford.github.io/lvrg-previews/{prospect_id}/index.html"
    
    email_prompt = f"""Write a cold outreach email from {SENDER_NAME} at {SENDER_AGENCY} to the owner/decision maker of {intel['business_name']}.

BUSINESS INTEL:
- Business: {intel['business_name']}
- Location: {intel['location']}
- Type: {intel.get('business_type', 'business')}
- Pain point: {intel.get('pain_point', '')}
- What's missing: {intel.get('missing', '')}
- Their CTA: {intel.get('cta_angle', '')}

We built them a free personalized preview website showing what their site could look like with AI-powered redesign + a live AI chat agent. They can claim it by booking a call.

Preview URL: {preview_url}
Booking link: {BOOKING_URL}

Write:
1. The email body (3-5 short paragraphs, conversational, no fluff, reference their specific business)
2. Three subject line variants:
   - A: Curiosity-driven (make them wonder)
   - B: Pain-point driven (call out the specific problem)
   - C: Benefit-driven (lead with the outcome)

Return as JSON:
{{
  "body": "...",
  "subject_a": "...",
  "subject_b": "...",
  "subject_c": "...",
  "recommended_subject": "b"
}}"""

    client = _get_client()
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": email_prompt}]
    )
    
    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.split("```")[0]
    
    try:
        email_data = json.loads(raw)
    except:
        email_data = {
            "body": raw,
            "subject_a": f"Quick question about {intel['business_name']}",
            "subject_b": f"Your website is losing you customers",
            "subject_c": f"Free preview site for {intel['business_name']}",
            "recommended_subject": "b"
        }
    
    # Grade determines angle (grade passed in from api.py)
    email_data["hook"] = "new_site" if grade.get("total", 5) <= 5 else "live_chat"
    
    return email_data
