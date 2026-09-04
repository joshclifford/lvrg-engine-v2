"""
LVRG Lead Magnet Engine V2 — Site + Email Generator
V2 improvements:
  - Tailwind CDN (no inline-style constraint)
  - Real photos from the prospect's own site, passed as <img> URLs
  - Real ratings/reviews passed in from the app — never scraped, never invented
  - Press/media mentions pulled in via Firecrawl search
  - Business-type design personalities
  - Better headline direction
"""

import concurrent.futures
import os
import re
import json
from typing import Optional
import anthropic

import cost
from claude_text import first_text
from config import SITES_DIR, BOOKING_URL, SENDER_NAME, SENDER_AGENCY

# Ceiling for a generated page. This is a cap, not a target — most pages come in
# well under it, so raising it costs nothing on a typical build and only helps
# the pages that were previously cut off. Deliberately not 128K: the caller
# aborts the whole engine call at 135s (build-smart-site ENGINE_TIMEOUT_MS) and
# generation already runs ~82s, so an unbounded ceiling would trade truncated
# pages for timed-out builds.
SITE_MAX_TOKENS = int(os.environ.get("SITE_MAX_TOKENS", "32000"))

# Ceiling for ONE page of a multi-page build. Deliberately well under
# SITE_MAX_TOKENS: today's single page covers 9 sections (claim bar through
# footer) in one shot, but one page of a multi-page build covers far fewer.
PAGE_MAX_TOKENS = int(os.environ.get("PAGE_MAX_TOKENS", "14000"))

# How many pages of a multi-page build to generate at once (24 Aug 2026,
# POD01-53 follow-up). Generating them one at a time — 4 sequential Claude
# calls for a 4-page build — is what pushed real builds past leadscraper's
# 170s abort window: the caller gave up, the generation thread kept running
# unattended (a Python thread cannot be cancelled), and because nothing but
# the deploy step reported its own outcome, a build that finished AFTER the
# caller left never got deployed and never got reported — the row just sat
# `building` until the 15-minute reaper reaped it, refunded it, and threw
# away a site that had already been generated for nothing.
#
# Not left unbounded: MAX_PAGES in pages.py is env-overridable too, so a
# future bump there must not silently fire an unbounded burst of concurrent
# requests at Anthropic. Defaults to today's own MAX_PAGES ceiling (4), so
# this is a no-op cap until either constant is raised past the other.
PAGE_GENERATION_CONCURRENCY = int(os.environ.get("PAGE_GENERATION_CONCURRENCY", "4"))

# Concurrent page generation means several requests can land on Anthropic at
# once instead of one at a time, which makes a 429 more likely to be hit at
# all — the SDK already retries 429/5xx with backoff (honoring Retry-After)
# at its default of 2 attempts, which was fine for one request at a time and
# is thin margin for a burst of up to PAGE_GENERATION_CONCURRENCY. Only the
# per-page call opts into the higher count; generate_site/generate_email keep
# the SDK default via a bare _get_client().
PAGE_GENERATION_MAX_RETRIES = int(os.environ.get("PAGE_GENERATION_MAX_RETRIES", "5"))

# Page generation does not deliberate — it transcribes a spec into HTML. There
# is no reasoning step here worth paying for.
#
# This has to be explicit now. claude-opus-4-5 never thought unless asked, so
# the calls below said nothing about it and got no thinking. claude-sonnet-5
# runs ADAPTIVE THINKING BY DEFAULT, and thinking tokens bill at the OUTPUT
# rate — the most expensive line on a build that is already ~90% output. The
# first measured builds ran 25-29 cents with thinking silently on.
#
# Disabled rather than `effort: "low"` because low effort still thinks, just
# less. If page quality drops, prefer {"type": "adaptive"} with
# output_config={"effort": "low"} over going back to the default.
NO_THINKING = {"type": "disabled"}


def _get_client(max_retries: int = 2):
    # 2 is the anthropic SDK's own default — passed explicitly so callers that
    # want more (concurrent multi-page generation, see PAGE_GENERATION_MAX_RETRIES)
    # have somewhere to say so without touching every other call site.
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    return anthropic.Anthropic(api_key=key, max_retries=max_retries)


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


def _pain_point_context(intel: dict, r6: Optional[dict]) -> str:
    """Blend Claude's site-content-extracted pain_point with the R6 audit's
    weakest pillar (when the caller sent one) — real audit signal on top of,
    not instead of, the extraction. Falls back to the extracted pain_point
    unchanged when r6 is absent, so every existing caller (MCP tool, smoke
    tests, direct API calls without R6 data) is byte-for-byte unaffected."""
    extracted = intel.get("pain_point", "")
    if not r6:
        return extracted

    pillar = r6.get("weakest_pillar")
    score = r6.get("weakest_pillar_score")
    notes = r6.get("weakest_pillar_notes", "")
    if not pillar:
        return extracted

    label = str(pillar).replace("_", " ").title()
    r6_line = f"R6 audit flags {label} as the weakest pillar"
    if isinstance(score, (int, float)):
        r6_line += f" ({score}/100)"
    if notes:
        r6_line += f": {notes}"

    parts = [p for p in (extracted, r6_line) if p]
    return " ".join(parts)


def _as_count(value) -> int | None:
    """A review count as an int, or None if it is not usable as one.

    intel["review_count"] is not guaranteed to be a number. It can come from
    leadscraper (a real int) or from extract_intel_with_claude, whose output is
    model-generated JSON — so "12", "1,204" and "no reviews" all turn up. A bare
    `value > 0` raises TypeError on a str in Python 3, which would turn a
    cosmetic prompt bug into a failed build.

    Anything that will not coerce becomes None, which routes to the no-count
    branch that tells the model not to state a review count at all. Refusing to
    guess is the whole point of this block.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        # Numbers first: json.loads gives 12.0 for `12.0`, and routing that
        # through str() produces "12.0", which int() refuses. OverflowError
        # covers inf/nan, which int() also refuses but differently.
        if isinstance(value, (int, float)):
            return int(value)
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None


def _build_chat_widget(intel: dict) -> str:
    """GHL (LeadConnector) chat widget — injected into every generated page.

    The agent itself is built and trained in GoHighLevel, so `intel` is no longer
    read here. The parameter stays so both call sites (single-page and
    multi-page) keep working untouched.
    """
    return """
<!-- GHL Chat Widget -->
<script src="https://widgets.leadconnectorhq.com/loader.js" data-resources-url="https://widgets.leadconnectorhq.com/chat-widget/loader.js" data-widget-id="6a906f149f17bc64b3a4a640"></script>
"""


def _build_chat_widget_legacy(intel: dict) -> str:
    """Previous self-hosted widget. Kept for reference / rollback — not called.

    Still relevant because already-deployed previews embed this markup and hit
    the Railway /chat endpoint, so that endpoint must stay alive.
    """
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


def _build_photo_block(intel: dict) -> str:
    """Real photos from the prospect's own site, or an explicit no-placeholder
    instruction — extracted from generate_site so generate_page can reuse the
    exact same rendering without a second, drifting copy."""
    photos = intel.get("photos", [])

    # Every photo here is offered, downloaded or not (POD01-124).
    #
    # _inline_photo_assets swaps in the bytes for the ones intel fetched; the
    # rest keep their original url and hotlink the prospect's server, exactly
    # as before. That is DELIBERATE, and the reason is where the engine runs:
    # a failed download does not mean a dead image, it means dead FOR US. This
    # process fetches from a Railway datacenter IP, and blanket datacenter
    # blocking is the single most common reason a fetch fails — the same
    # failure that killed the old Yelp photo source and is written up at the
    # top of intel.py. It also covers a photo over _MAX_PHOTO_BYTES or slower
    # than _PHOTO_FETCH_TIMEOUT: both load fine in a real browser on a real
    # connection.
    #
    # So dropping those would trade a page that shows the prospect's actual
    # photos for a gradient, on nothing better than a guess that our IP's
    # experience matches the recipient's. Hotlinking is the weaker outcome,
    # not a broken one, and it is strictly no worse than every build shipped
    # before rehosting existed.
    #
    # The gradient path below still exists for its original case: no photos
    # found on the page at all.

    if photos:
        return f"""REAL PHOTOS (these are from the business's own website — use them as actual <img> tags, link directly to the URLs):
{chr(10).join(f'  {i+1}. {url}' for i, url in enumerate(photos[:4]))}
Use the best photo as the hero background (as an <img> with object-fit:cover, or as a CSS background-image url()).
Use others in gallery/services sections where they fit naturally.
Only use a photo if it makes sense in context — don't force it."""
    return "PHOTOS: No real photos available. Use CSS gradients and brand colors only — no placeholder images."


def _build_reviews_block(intel: dict) -> str:
    # Build reviews block. Rating and review count are real data passed in from
    # the app (Google Maps via Apify), never scraped.
    #
    # There is deliberately NO branch that emits review QUOTES. `intel["reviews"]`
    # is set to [] by scrape_site and nothing populates it — _merge_known has no
    # `reviews` key and leadscraper never sends one — so the branch that used to
    # live here was unreachable, and it still carried the instruction "use these
    # verbatim as testimonials". That instruction came from the Yelp scrape,
    # whose regex matched any JSON field named "text" anywhere on the page (ad
    # copy, category blurbs, other businesses' content) and handed the results
    # to the model as real customer quotes — worse than inventing one.
    # Do not add a quotes branch back without a verified review source behind it.
    #
    # Rating and count arrive independently: Apify returns plenty of listings
    # with one and not the other, and interpolating a missing count publishes the
    # literal string "from None reviews".
    #
    # Resolved against 1b89852 (Hamza, same bug, same day). That commit fixed the
    # None rendering but KEPT the `if reviews:` branch — which is unreachable
    # (nothing populates intel["reviews"]) and still carried "use these verbatim
    # as testimonials". Deleting it is the point of this change, so the deletion
    # wins and rating_stat goes with it.
    # `> 0`, not just `is not None`: a count of exactly zero passed the None
    # check and published "rated 4.5★ from 0 reviews. Use that as a stat." —
    # which is not social proof, it is an own goal on the prospect's own
    # branding. Zero is the absence of reviews, so it takes the no-count branch
    # below, which tells the model not to state one.
    rating = intel.get("rating")
    review_count = _as_count(intel.get("review_count"))

    if rating is not None and review_count is not None and review_count > 0:
        return (
            f"SOCIAL PROOF: This business is rated {rating}★ from {review_count} reviews. "
            f"Use that as a stat. You have NO review text — do NOT write testimonial quotes."
        )
    elif rating is not None:
        return (
            f"SOCIAL PROOF: This business is rated {rating}★. Use that as a stat. "
            f"You were NOT given a review count — do not state one. "
            f"You have NO review text — do NOT write testimonial quotes."
        )
    return (
        "REVIEWS: None available. Do NOT write testimonial quotes, do NOT invent "
        "star ratings, and do NOT add a testimonials section."
    )


def _build_press_block(intel: dict) -> str:
    press_mentions = intel.get("press_mentions", [])
    if press_mentions:
        return f"""PRESS MENTIONS (use these as credibility signals — quote them if there's a good quote):
{chr(10).join(f'  - {p["source"]}: "{p["title"]}"' + (f' — "{p["quote"]}"' if p.get("quote") else '') for p in press_mentions[:3])}"""
    return "PRESS: No press mentions found."


def _build_social_block(intel: dict) -> str:
    """Social profiles come from the app (Apify), merged in by api.py."""
    socials = intel.get("socials") or {}
    social_links = "\n".join(f"  {k.replace('_url', '').title()}: {v}" for k, v in socials.items())
    return f"SOCIAL PROFILES (link these in the footer):\n{social_links}" if social_links else ""


def _strip_markdown_fences(html: str) -> str:
    if html.startswith("```"):
        html = re.sub(r'^```[a-z]*\n?', '', html)
        html = re.sub(r'\n?```$', '', html)
    return html


def _close_truncated_html(html: str) -> str:
    """Close a truncated response before anything else touches it. Without
    this, widget/nav injection staples itself onto an unclosed document and
    we publish malformed HTML. Parity with v1 generator.py."""
    if not html.rstrip().endswith("</html>"):
        if "</body>" not in html:
            html += "\n</body>"
        html += "\n</html>"
    return html


def generate_site(intel: dict, prospect_id: str, notes: str = "", r6: Optional[dict] = None,
                  meter=None, photo_assets: Optional[dict] = None) -> str:
    """Generate a complete single-file HTML site for a prospect. Returns folder path.

    `meter` is an optional cost.CostMeter. Keyword-only in practice and
    defaulting to None so lm-tool, run_engine.py and the tests — none of which
    care what a build cost — call this exactly as they did before.
    """

    print(f"  [generator] Generating V2 site for {intel['business_name']}...")

    notes_block = f"\n\nSPECIAL INSTRUCTIONS:\n{notes}\n" if notes else ""
    design = _get_design_personality(intel.get("business_type", "other"))
    pain_point = _pain_point_context(intel, r6)

    photo_block = _build_photo_block(intel)
    reviews_block = _build_reviews_block(intel)
    press_block = _build_press_block(intel)
    social_block = _build_social_block(intel)

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
- Pain point: {pain_point}
- CTA: {intel.get('cta_angle', 'Get in Touch')}

━━━ REAL CONTENT ━━━
{photo_block}

{reviews_block}

{press_block}

{social_block}

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

4. SOCIAL PROOF BAR — only stats given to you above (rating, review count,
   years open, awards). If none were given, omit this section entirely.

5. SERVICES/MENU — 3 feature cards using their REAL services

6. TESTIMONIALS — ONLY the review quotes provided above, verbatim.
   If none were provided, OMIT this section. Never invent a quote, a customer
   name, or a star rating.

7. PRESS / AS SEEN IN — if press mentions provided, show source logos as text badges.
   Skip this section entirely if no press.

8. CTA BANNER — headline + copy driving toward: {intel.get('cta_angle', 'booking')}

9. FOOTER — {intel.get('location','')}, {intel.get('phone','')}, hours

━━━ COPY RULES ━━━
- Reference {intel.get('neighborhood') or intel.get('location','').split(',')[0]} naturally in copy
- Every CTA drives toward: {intel.get('cta_angle', 'booking')}
- Address this pain point: {pain_point}
- NEVER write fake testimonials — if no real reviews, skip quotes entirely

━━━ OUTPUT ━━━
Return ONLY the complete HTML. No explanation. No markdown fences. No chat widget (injected separately).
Start with <!DOCTYPE html>"""

    client = _get_client()
    # Streamed, not create(). A full page runs well past the SDK's non-streaming
    # HTTP timeout at this token ceiling; streaming also lets max_tokens rise
    # without the request dying mid-generation. get_final_message() gives the
    # same object create() would have returned.
    with client.messages.stream(
        model="claude-sonnet-5",
        thinking=NO_THINKING,
        max_tokens=SITE_MAX_TOKENS,
        messages=[{"role": "user", "content": site_prompt}],
    ) as stream:
        response = stream.get_final_message()
    cost.record(meter, "site", "claude-sonnet-5", response)

    if response.stop_reason == "max_tokens":
        print(f"  [generator] WARNING: hit max_tokens ({SITE_MAX_TOKENS}) — page may be cut short")

    html = first_text(response).strip()
    html = _strip_markdown_fences(html)
    html = _close_truncated_html(html)
    html = _inline_photo_assets(html, photo_assets)

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


def _inline_photo_assets(html: str, assets: Optional[dict]) -> str:
    """Swap the prospect's photo URLs for the bytes intel downloaded (POD01-124).

    Takes the ASSET MAP, never the intel dict. These blobs must not travel on
    `intel`: api.py streams that dict to the caller and leadscraper writes it
    to `businesses.smart_site_intel`, a column its All Leads query selects for
    every row on screen. Measured, four real photos took one lead's intel from
    6.7 KB to 771 KB — a 25-row page from 166 KB to 18.8 MB. leadSelect.ts's
    own header records the last time that column class was allowed to grow.

    Runs AFTER generation, on purpose. _build_photo_block still hands Claude the
    original URLs, so the prompt is byte-for-byte what it was and the model's
    output cannot drift because of this change. It also keeps base64 out of the
    token stream, where four photos would cost more than the rest of the build
    put together.

    Applied in all three producers rather than at their call sites, so a new
    caller cannot reintroduce the bug by forgetting it. Idempotent — a second
    pass finds no original URLs left to match.

    Substitutes only URLs that occur in THIS page's html, so a sub-page using
    two of the four photos carries two, not four. Plain string replace is safe:
    measured across every live preview, an image src is never HTML-escaped
    (0 of 24 query-string srcs escape their &).
    """
    if not assets:
        return html
    for url, data_uri in assets.items():
        if url in html:
            html = html.replace(url, data_uri)
    return html


def _inject_base_href(html: str, prospect_id: str) -> str:
    """The public preview URL is {app}/preview/{slug} — no trailing slash, no
    filename. A browser resolves a bare relative link like href="about.html"
    against everything up to the LAST slash in the current URL, which is
    ".../preview/" — dropping the slug entirely and 404ing. A <base> tag
    fixes this for every relative link on the page at once. Root-relative
    (not a full origin URL) since the preview is always same-origin with the
    app. Claude has no way to know the final proxy hostname or slug, so this
    has to be mechanical, unlike the nav bar itself."""
    base_tag = f'<base href="/preview/{prospect_id}/">'
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n  {base_tag}", 1)
    return html.replace("<!DOCTYPE html>", f"<!DOCTYPE html>\n{base_tag}", 1)


def _fix_absolute_page_links(html: str, pages_plan: list) -> str:
    """Claude may write href="/about.html" (leading slash) out of training
    habit despite being told to use relative links. A leading slash bypasses
    <base> entirely and resolves to the app's own root instead of the
    preview. Cheap, targeted fix: only rewrite hrefs matching a filename we
    actually planned, never a blanket leading-slash strip (which could touch
    an unrelated absolute link Claude legitimately wrote, e.g. to BOOKING_URL
    if it happened to be root-relative)."""
    for page in pages_plan:
        filename = page["filename"]
        html = html.replace(f'href="/{filename}"', f'href="{filename}"')
        html = html.replace(f"href='/{filename}'", f"href='{filename}'")
    return html


def generate_page(
    intel: dict,
    design: dict,
    page: dict,
    nav: list,
    notes: str = "",
    r6: Optional[dict] = None,
    meter=None,
    photo_assets: Optional[dict] = None,
) -> str:
    """Generate ONE page of a multi-page site. Returns raw HTML — does not
    write to disk (generate_multi_page_site owns the filesystem). `design`
    must be resolved ONCE by the caller and passed in unchanged for every
    page in the build, never re-derived per page — that's what keeps a
    4-page site looking like one business instead of four.

    `nav` is the full page plan for this build, THIS page included — the
    prompt renders the nav bar and footer from this exact list so every page
    links to every other page, with no dead ends. Navigation is built
    in-prompt, not spliced in afterward: it has to inherit the same Tailwind
    classes/fonts/colors Claude just chose for this page's content, which a
    mechanical post-injection (like the chat widget) can't do without risking
    the "every page looks like a different website" failure this whole
    feature exists to avoid.
    """
    notes_block = f"\n\nSPECIAL INSTRUCTIONS:\n{notes}\n" if notes else ""
    pain_point = _pain_point_context(intel, r6)

    photo_block = _build_photo_block(intel)
    reviews_block = _build_reviews_block(intel)
    press_block = _build_press_block(intel)
    social_block = _build_social_block(intel)

    nav_lines = "\n".join(
        f'  - "{p["title"]}" → {p["filename"]}' + ("  (THIS PAGE — mark active)" if p["slug"] == page["slug"] else "")
        for p in nav
    )
    is_home = page["slug"] == "index"

    body_structure = (
        """3. HERO — follow the hero style for this business type above.
   Headline rule: Make it UNEXPECTED and SPECIFIC. Own something.
   Bad: "San Diego's Premier Cocktail Experience"
   Good: "The bar North Park didn't know it needed — until now"
   One bold headline + one supporting line. Two CTAs.

4. SOCIAL PROOF BAR — only stats given to you above. If none were given, omit entirely.

5. SERVICES/MENU — 3 feature cards using their REAL services

6. TESTIMONIALS — ONLY the review quotes provided above, verbatim. If none, OMIT.

7. PRESS / AS SEEN IN — if press mentions provided, show as text badges. Skip if none.

8. CTA BANNER — headline + copy driving toward: """ + intel.get('cta_angle', 'booking')
        if is_home
        else f"""3. PAGE CONTENT — this page is "{page['title']}". {page['brief']}
   Build 2-4 sections appropriate to this page's purpose. Reuse the real
   content blocks above (photos, reviews, press) only where they genuinely
   fit this page's topic — do not force Home's exact section list onto a
   page that isn't Home.

4. CTA SECTION — headline + copy driving toward: """ + intel.get('cta_angle', 'booking')
    )

    page_prompt = f"""You are building ONE PAGE of a multi-page high-end preview website for {intel['business_name']}. This page is "{page['title']}".{notes_block}

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
- Pain point: {pain_point}
- CTA: {intel.get('cta_angle', 'Get in Touch')}

━━━ REAL CONTENT ━━━
{photo_block}

{reviews_block}

{press_block}

{social_block}

━━━ DESIGN PERSONALITY (must match every other page in this site exactly) ━━━
Mood: {design['mood']}
Fonts: {design['fonts']}
Layout approach: {design['layout']}
Hero style: {design['hero_style']}
Reference aesthetic: {design['references']}

━━━ TECH STACK ━━━
Use Tailwind CSS via CDN — include this in <head>:
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: '{intel.get('primary_color','#f59e0b')}' }} }} }} }}</script>
Use Google Fonts matching the design personality above — the SAME fonts on every page.
This gives you full Tailwind (hover:, focus:, responsive, animations) — use it fully.
NO inline style= attributes. Use Tailwind classes exclusively.

━━━ THIS SITE'S PAGES ━━━
This build has {len(nav)} page(s). Every page shares this exact nav bar and footer:
{nav_lines}
Link with RELATIVE filenames only (href="about.html") — NEVER a leading slash
(href="/about.html") and NEVER a full URL. Give this page's own nav entry a
visually distinct active treatment (different weight, underline, or accent
color) so a visitor always knows which page they're on. The footer repeats
the same links.

━━━ STRUCTURE ━━━
This is a single HTML page ({page['filename']}), one of {len(nav)} pages in this build.

1. CLAIM BAR — sticky, black bg, centered single line:
   "This site was built for **{intel['business_name']}** by LVRG Agency"
   + gold pill "Claim This Site →" button linking to {BOOKING_URL}
   Everything centered on one line. No left/right split.

2. NAV — business name as logo, links to every page listed above (active page marked), primary CTA button

{body_structure}

9. FOOTER — {intel.get('location','')}, {intel.get('phone','')}, hours, and the same page links as the nav (active page marked)

━━━ COPY RULES ━━━
- Reference {intel.get('neighborhood') or intel.get('location','').split(',')[0]} naturally in copy
- Every CTA drives toward: {intel.get('cta_angle', 'booking')}
- Address this pain point: {pain_point}
- NEVER write fake testimonials — if no real reviews, skip quotes entirely
- Do not repeat another page's content verbatim — each page earns its place

━━━ OUTPUT ━━━
Return ONLY the complete HTML. No explanation. No markdown fences. No chat widget (injected separately).
Start with <!DOCTYPE html>"""

    client = _get_client(max_retries=PAGE_GENERATION_MAX_RETRIES)
    with client.messages.stream(
        model="claude-sonnet-5",
        thinking=NO_THINKING,
        max_tokens=PAGE_MAX_TOKENS,
        messages=[{"role": "user", "content": page_prompt}],
    ) as stream:
        response = stream.get_final_message()
    cost.record(meter, f"page:{page['slug']}", "claude-sonnet-5", response)

    if response.stop_reason == "max_tokens":
        print(f"  [generator] WARNING: hit max_tokens ({PAGE_MAX_TOKENS}) on page {page['filename']} — may be cut short")

    html = first_text(response).strip()
    html = _strip_markdown_fences(html)
    html = _close_truncated_html(html)
    html = _inline_photo_assets(html, photo_assets)
    return html


# ── Get Listed / Sponsored Story lead magnets ───────────────────────────────
# Reuse the exact scrape → AI-mockup → gotheresandiego.com → chat-widget
# pipeline the Free Website magnet already proves out. Only the prompt
# content differs — no new hosting, deploy, or widget work needed. See
# project_aug31_sponsored_story_and_get_listed_build_plan (PM memory) for
# why these two exist and why they're built this way.
#
# Both offers are already fully priced/positioned in TSD's own docs
# (docs/TSD-Docs/client-originals/campaign-get-listed.md,
# campaign-advertising.md) — this only builds the per-prospect MOCKUP,
# which neither doc described, not the offer itself.

GET_LISTED_VERTICAL_FRAMING = {
    "realtor": (
        "a real estate agent",
        "their current listings, their neighborhoods of focus, and their track record",
    ),
    "contractor": (
        "an independent contractor",
        "their trade specialty, past project photos, and service area",
    ),
}


def generate_offer_lead_magnet_page(
    offer: str,
    intel: dict,
    vertical: Optional[str] = None,
    meter=None,
    photo_assets: Optional[dict] = None,
) -> str:
    """Generate a single-page mockup for the Get Listed or Sponsored Story
    lead magnet. `offer` is "get_listed" or "sponsored_story". `vertical`
    only applies to get_listed today ("realtor" or "contractor", per Josh's
    own examples on the 27 Aug call) — defaults to a generic framing when
    omitted or unrecognized, so an unknown vertical degrades gracefully
    instead of failing the build.

    Mirrors generate_page's shape (same photo/reviews/press/social blocks,
    same Tailwind/CDN tech stack, same claim-bar + nav + footer structure)
    so a lead magnet built here looks like it belongs next to a Free
    Website preview, not a different product. The chat widget is injected
    separately by the caller, same as every other generated page.
    """
    photo_block = _build_photo_block(intel)
    reviews_block = _build_reviews_block(intel)
    press_block = _build_press_block(intel)
    social_block = _build_social_block(intel)

    if offer == "get_listed":
        role, detail_hint = GET_LISTED_VERTICAL_FRAMING.get(
            vertical, ("a local business", "their services and what makes them worth choosing")
        )
        page_purpose = f"""This is a MOCK-UP of how {intel['business_name']} — {role} — would look
featured in the ThereSanDiego.com business directory, as a personalized preview to close a
Get Listed ($297 one-time, permanent profile) prospect. Build it around {detail_hint}.

STRUCTURE (this is a directory profile mock-up, not a full website):
1. CLAIM BAR — sticky, same as every LVRG preview:
   "This is a preview of your ThereSanDiego.com listing" + gold pill "Claim This Listing →" linking to {BOOKING_URL}
2. PROFILE HEADER — business name, {role} framing, location/neighborhood, primary photo
3. ABOUT — 2-3 sentences on {detail_hint}, in ThereSanDiego's warm local-guide voice
4. WHY LIST HERE — 3 short points: permanent page (no monthly fee), SEO-optimized so San Diegans searching for what you offer find you, person+business schema (nobody else tells Google who you are as the person behind the business)
5. GALLERY — real photos if provided, otherwise omit
6. TESTIMONIALS — ONLY real review quotes provided, verbatim. If none, omit entirely.
7. CTA — "Claim this listing for $297, one time, permanent" driving to {BOOKING_URL}
8. FOOTER — location, phone, hours"""
    elif offer == "sponsored_story":
        page_purpose = f"""This is a MOCK-UP of a Sponsored Story: a short editorial feature as it would
run on ThereSanDiego.com and get promoted to their 70,000+ monthly audience, built to close an
Advertising prospect via the $197 First Look entry offer.

STRUCTURE (this is an editorial feature mock-up, not a full website):
1. CLAIM BAR — sticky, same as every LVRG preview:
   "This is a preview of your Sponsored Story" + gold pill "Claim This Feature →" linking to {BOOKING_URL}
2. ARTICLE HEADER — a real editorial-style headline about {intel['business_name']} (not a generic "About Us" title), byline "There San Diego Staff", hero photo if provided
3. THE STORY — 3-4 short paragraphs written in ThereSanDiego's warm, locals-know-locals editorial voice, using their REAL description/services/neighborhood — this should read like a real feature article a San Diegan would actually enjoy reading, not an ad
4. PULL QUOTE — one real review quote if provided, styled as an editorial pull-quote. If none, omit.
5. GUARANTEE CALLOUT — "Every Sponsored Story comes with guaranteed impressions. If we don't hit the number, we keep promoting until we do." + "First Look: $197 one-time, puts you in front of the audience so you can see what it does."
6. CTA — driving to {BOOKING_URL}
7. FOOTER — location, phone, hours"""
    else:
        raise ValueError(f"Unknown offer for generate_offer_lead_magnet_page: {offer!r}")

    page_prompt = f"""You are building a personalized lead-magnet PREVIEW PAGE for {intel['business_name']}.
This is NOT a full business website — see the specific structure below for what it actually is.

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

━━━ REAL CONTENT ━━━
{photo_block}

{reviews_block}

{press_block}

{social_block}

━━━ TECH STACK ━━━
Use Tailwind CSS via CDN — include this in <head>:
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: '{intel.get('primary_color','#f59e0b')}' }} }} }} }}</script>
Use Google Fonts matching the brand vibe above.
NO inline style= attributes. Use Tailwind classes exclusively.

━━━ WHAT TO BUILD ━━━
{page_purpose}

━━━ COPY RULES ━━━
- Reference {intel.get('neighborhood') or intel.get('location','').split(',')[0]} naturally
- NEVER write fake testimonials — if no real reviews, skip quotes entirely
- NEVER invent pricing beyond what's given above
- Single page, no nav to other pages — this is a standalone lead magnet, not a multi-page site

━━━ OUTPUT ━━━
Return ONLY the complete HTML. No explanation. No markdown fences. No chat widget (injected separately).
Start with <!DOCTYPE html>"""

    client = _get_client(max_retries=PAGE_GENERATION_MAX_RETRIES)
    with client.messages.stream(
        model="claude-sonnet-5",
        thinking=NO_THINKING,
        max_tokens=PAGE_MAX_TOKENS,
        messages=[{"role": "user", "content": page_prompt}],
    ) as stream:
        response = stream.get_final_message()
    cost.record(meter, f"offer_page:{offer}", "claude-sonnet-5", response)

    if response.stop_reason == "max_tokens":
        print(f"  [generator] WARNING: hit max_tokens ({PAGE_MAX_TOKENS}) on a {offer} lead magnet for {intel['business_name']} — may be cut short")

    html = first_text(response).strip()
    html = _strip_markdown_fences(html)
    html = _close_truncated_html(html)
    html = _inline_photo_assets(html, photo_assets)
    return html


def generate_multi_page_site(
    intel: dict,
    prospect_id: str,
    pages_plan: list,
    notes: str = "",
    r6: Optional[dict] = None,
    meter=None,
    photo_assets: Optional[dict] = None,
) -> dict:
    """Generate every planned page CONCURRENTLY, capped at
    PAGE_GENERATION_CONCURRENCY at a time (24 Aug 2026 — was strictly
    sequential; see PAGE_GENERATION_CONCURRENCY's comment for why that
    stopped being safe once real builds started missing leadscraper's abort
    window).

    Resolves the design personality ONCE and threads it into every page.
    Wipes and recreates the prospect's folder first: a build that plans fewer
    pages on a retry (e.g. Services no longer qualifies) must not leave a
    stale services.html sitting in the folder for deploy_site to push
    alongside the new set.

    Still fails fast in the sense that matters — if any page's call raises,
    the exception propagates and NOTHING gets deployed, so a nav bar with
    dead links never ships. What concurrency gives up is stopping EARLY: all
    planned pages are already in flight by the time one of them fails, so a
    failure no longer saves the cost of the pages behind it in the old
    sequential order. That's the trade for not letting one slow page hold up
    every page after it — the failure case was already the expensive,
    refunded path; this just also makes the success case (the overwhelming
    majority) 2-4x faster instead of piling every page's latency in series.

    Returns {slug: absolute_file_path}, files written in `pages_plan`'s
    order regardless of which page's Claude call actually finished first.
    """
    import shutil

    design = _get_design_personality(intel.get("business_type", "other"))

    site_dir = os.path.join(SITES_DIR, prospect_id)
    if os.path.isdir(site_dir):
        shutil.rmtree(site_dir)
    os.makedirs(site_dir, exist_ok=True)

    widget_html = _build_chat_widget(intel)

    def _build_one(page: dict) -> str:
        print(f"  [generator] Generating page '{page['title']}' ({page['filename']}) for {intel['business_name']}...")
        # One shared meter across every worker thread — CostMeter.record takes
        # a lock precisely so this fan-out can bill into it concurrently.
        html = generate_page(intel, design, page, pages_plan, notes, r6,
                             meter=meter, photo_assets=photo_assets)
        html = _inject_base_href(html, prospect_id)
        html = _fix_absolute_page_links(html, pages_plan)
        if "</body>" in html:
            html = html.replace("</body>", widget_html + "\n</body>")
        else:
            html += widget_html
        return html

    # Bounded, not "however many pages_plan happens to hold" — pages.py's own
    # MAX_PAGES is separately env-overridable, and this cap is what stops a
    # future bump there from silently turning into an unbounded burst of
    # concurrent requests against Anthropic.
    max_workers = max(1, min(PAGE_GENERATION_CONCURRENCY, len(pages_plan)))
    html_by_slug: dict[str, str] = {}
    first_error: Exception | None = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_build_one, page): page for page in pages_plan}
        # ThreadPoolExecutor's own __exit__ blocks until every submitted call
        # finishes regardless — a raised in-flight Claude call cannot be
        # cancelled, so this loop draining as_completed just makes that wait
        # explicit and lets every page's own error surface (not just the
        # first one observed) instead of only the one __exit__ happens to hit.
        for future in concurrent.futures.as_completed(futures):
            page = futures[future]
            try:
                html_by_slug[page["slug"]] = future.result()
            except Exception as e:
                print(f"  [generator] page '{page['title']}' ({page['filename']}) failed: {e}")
                if first_error is None:
                    first_error = e

    if first_error is not None:
        raise first_error

    site_paths = {}
    for page in pages_plan:
        file_path = os.path.join(site_dir, page["filename"])
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_by_slug[page["slug"]])
        site_paths[page["slug"]] = file_path

    print(f"  [generator] ✓ {len(site_paths)}-page V2 site saved to {site_dir}")
    return site_paths


def generate_email(intel: dict, grade: dict, prospect_id: str, r6: Optional[dict] = None,
                   meter=None) -> dict:
    """Generate cold outreach email. grade and prospect_id kept for api.py compatibility."""
    preview_url = f"https://joshclifford.github.io/lvrg-previews/{prospect_id}/index.html"
    pain_point = _pain_point_context(intel, r6)

    email_prompt = f"""Write a cold outreach email from {SENDER_NAME} at {SENDER_AGENCY} to the owner/decision maker of {intel['business_name']}.

BUSINESS INTEL:
- Business: {intel['business_name']}
- Location: {intel['location']}
- Type: {intel.get('business_type', 'business')}
- Pain point: {pain_point}
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
        model="claude-sonnet-5",
        thinking=NO_THINKING,
        max_tokens=1500,
        messages=[{"role": "user", "content": email_prompt}]
    )
    cost.record(meter, "email", "claude-sonnet-5", response)

    raw = first_text(response).strip()
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
