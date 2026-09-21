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
from datetime import date
from html import escape, unescape
from typing import Optional
import anthropic

import cost
from claude_text import first_text
from config import (SITES_DIR, BOOKING_URL, build_booking_url, SENDER_NAME, SENDER_AGENCY,
                    PUBLISHER_NAME, PREVIEW_PUBLIC_BASE)
# One definition of "the same host", shared with the slug builder. Comparing
# raw href strings would miss www. and a trailing slash.
from slug import canonical_domain

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

# Ceiling for a Get Listed / Sponsored Story lead magnet. These are STANDALONE
# full pages, so they belong with SITE_MAX_TOKENS, not with the multi-page
# fragment budget above. They ran on PAGE_MAX_TOKENS until 7 Sep 2026, when the
# two magnets grew to 11 and 10 sections: overflow truncates the TAIL, which is
# now the three-tier pricing table and the CTA, and _close_truncated_html
# repairs the markup so the page still renders. That failure is silent and it
# ships a preview with nothing to click.
OFFER_PAGE_MAX_TOKENS = int(os.environ.get("OFFER_PAGE_MAX_TOKENS", str(SITE_MAX_TOKENS)))

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


# The one wording every prompt uses for "you were given no review text".
#
# Kept as one constant because the previous wording was written out three times
# and every copy banned the same single shape: a quote attributed to a CUSTOMER,
# with a name under it. The live Pop Pie Co Sponsored Story published
#
#   "Yelp reviewers have specifically called out the key lime pie after a
#    recipe overhaul, calling it exactly what 'was needed'"
#
# which names no customer and so broke none of them, while being the same
# fabrication: a quoted fragment attributed to real third parties on a real
# platform, on a page emailed to the business itself as a pitch. The business
# owner is one search away from finding the quote does not exist (POD01-133).
#
# So the rule is about the ATTRIBUTION, not the shape of the sentence. Naming
# the platforms matters: the model reached for "Yelp reviewers" specifically,
# and a rule phrased only around "customers" reads as permission for the rest.
NO_REVIEW_TEXT_RULE = (
    "YOU WERE GIVEN NO REVIEW TEXT. Not one review, not a snippet, not a summary. "
    "So you cannot report what any reviewer said, in any form:\n"
    "- No quotation marks around anything a customer, reviewer or visitor is said to have said.\n"
    "- No unquoted paraphrase either. \"Reviewers praise the pastries\" is the same claim "
    "without the punctuation, and is equally fabricated.\n"
    "- No attributing a REVIEW OR AN OPINION to Yelp, Google, TripAdvisor, Facebook or any "
    "other platform, or to \"reviewers\", \"regulars\", \"locals\" or \"customers\" as a group. "
    "An unnamed group is not safer than a named person, it is only harder to check.\n"
    "- No claims about what reviewers \"often mention\", \"call out\", \"rave about\" or \"agree on\".\n"
    "The star rating and review count above, if any were given, are the ONLY things you know "
    "about this business's reviews. Report those as bare numbers and stop there.\n"
    "This is a ban on REPORTING OPINIONS YOU WERE NOT GIVEN, not on the local voice. Writing "
    "warmly about the place, the neighborhood and who it is for is the whole job and stays. "
    "\"A North Park favorite for twenty years\" is fine if their own copy says so. "
    "\"Locals say it is a North Park favorite\" is not, because you were told no such thing."
)


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
            f"Use that as a stat, and nothing beyond it.\n{NO_REVIEW_TEXT_RULE}"
        )
    elif rating is not None:
        return (
            f"SOCIAL PROOF: This business is rated {rating}★. Use that as a stat. "
            f"You were NOT given a review count, so do not state one.\n{NO_REVIEW_TEXT_RULE}"
        )
    return (
        "REVIEWS: None available. Do NOT invent star ratings and do NOT add a "
        f"testimonials section.\n{NO_REVIEW_TEXT_RULE}"
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


# The booking page as a comparable (host, path) pair, derived from BOOKING_URL
# rather than written out again so moving the booking page moves this with it.
_BOOKING_HOST = canonical_domain(BOOKING_URL)


def _booking_path(url: str) -> str:
    """The path part of a URL, without query, fragment or trailing slash."""
    path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", (url or "").strip(), flags=re.IGNORECASE)
    return path.split("?")[0].split("#")[0].rstrip("/").lower()


_BOOKING_PATH = _booking_path(BOOKING_URL)

# Backreference on the opening quote, so a single quote inside a double-quoted
# href does not end the match early.
_HREF_RE = re.compile(r"""(href\s*=\s*)(["'])([^"']*)\2""", re.IGNORECASE)


def _is_booking_href(href: str) -> bool:
    """Does this href point at the booking page, whatever it carries after it?"""
    return (
        canonical_domain(href) == _BOOKING_HOST
        and _booking_path(href) == _BOOKING_PATH
    )


def _attribute_booking_links(html: str, booking_url: str, label: str) -> str:
    """Force every booking link on the page to carry its attribution params.

    The prompt asks for the tracked URL on every CTA, and asking is not the same
    as knowing (POD01-130). The first Get Listed page built after the params
    shipped carried both its CTAs bare, while the Sponsored Story built eighteen
    minutes earlier on the same code carried all five. Nothing caught it because
    nothing was looking, and a page whose CTAs are silently unattributed reads as
    a page nobody clicked.

    Rewriting rather than warning: the destination is identical either way, so
    there is no judgement call to leave to the model. Same reasoning as
    _inject_base_href, which stopped asking for relative links to resolve and
    made them resolve. The warning stays so prompt drift is still visible.

    Runs before photo inlining so a base64 blob cannot be scanned for hrefs.
    """
    rewritten = 0

    def _swap(match: "re.Match") -> str:
        nonlocal rewritten
        prefix, quote, href = match.group(1), match.group(2), match.group(3)
        if not _is_booking_href(href):
            return match.group(0)
        # &amp; is the same link, correctly escaped. Rewriting it would fire the
        # warning on every build, and a warning that always fires is one nobody
        # reads.
        if href.replace("&amp;", "&") == booking_url:
            return match.group(0)
        rewritten += 1
        return f"{prefix}{quote}{booking_url}{quote}"

    html = _HREF_RE.sub(_swap, html)
    if rewritten:
        print(
            f"  [generator] WARNING: {label} carried {rewritten} untracked booking "
            f"link(s); rewritten to the attributed URL (POD01-130)."
        )
    return html


# ── What a Sponsored Story is sold as (POD01-126) ────────────────────────────
#
# A Sponsored Story is sold as a real published article: something a search
# engine can read, and something that renders as a proper card with a picture
# when There San Diego promotes it on Facebook or Instagram. The generated page
# had a real editorial <title> and nothing else — no description, no Open Graph,
# no canonical, no structured data — so the story shared as a bare link with no
# image and no summary, on the one channel the offer is sold on.
#
# Stamped on in code rather than asked for in the prompt. Six head tags asked
# for is six chances to lose one silently, and that is not hypothetical: the
# booking links were asked for and came back bare on a live page the same week
# (POD01-130). Everything below is read out of the page the model already wrote
# or out of real intel, so nothing here is invented.

_TAG_RE = re.compile(r"<[^>]+>")

# Short enough to survive a search result without being cut, which is the whole
# job of a meta description.
_META_DESCRIPTION_CHARS = 155

# A paragraph long enough to be the story rather than the claim bar. The first
# <p> in the document is "This is a preview of your Sponsored Story", which
# describes the mockup and says nothing about the business.
_STORY_PARAGRAPH_CHARS = 80


def _visible_text(fragment: str) -> str:
    """Markup out, entities decoded, whitespace collapsed to single spaces."""
    return unescape(" ".join(_TAG_RE.sub(" ", fragment).split()))


def _first_tag_text(html: str, tag: str) -> str:
    """The visible text of the first <tag> in the document, or ""."""
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
    return _visible_text(match.group(1)) if match else ""


def _meta_description(html: str, intel: dict) -> str:
    """One real sentence about the business, cut to fit a search result.

    The story's own opening paragraph, which the model wrote from their real
    description and services. Falling back to the scraped description, and to
    nothing at all rather than to filler: an invented summary on a page sold as
    editorial is the same failure _warn_fabricated_reviews exists to catch.

    Cut on a word boundary. A description sliced mid-word reads as broken, and
    Google shows it exactly as given.
    """
    paragraphs = [
        _visible_text(p)
        for p in re.findall(r"<p\b[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
    ]
    source = next(
        (p for p in paragraphs if len(p) >= _STORY_PARAGRAPH_CHARS),
        (intel.get("description") or "").strip(),
    )
    if not source:
        return ""
    if len(source) <= _META_DESCRIPTION_CHARS:
        return source
    return source[:_META_DESCRIPTION_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _first_remote_image(html: str) -> str:
    """The first <img> that still points at a real address.

    Read BEFORE _inline_photo_assets. After it the same src is a data: URI, and
    a social crawler cannot fetch bytes embedded in a page it has not parsed —
    og:image has to be somewhere Facebook can go and GET.
    """
    match = re.search(
        r"""<img\b[^>]*\bsrc\s*=\s*["'](https?://[^"']+)["']""", html, re.IGNORECASE
    )
    return match.group(1) if match else ""


def preview_page_url(public_base: str, prospect_id: str) -> str:
    """The page's own public address, or "" when the caller did not say.

    Returns empty rather than guessing. A canonical tag naming the wrong URL is
    worse than no canonical tag: it tells a crawler the real page is somewhere
    else, and the somewhere else is a 404. leadscraper holds this value in
    SMART_SITE_PUBLIC_BASE and passes it in; PREVIEW_PUBLIC_BASE is the fallback
    for run_engine.py and smoke runs, which have no app to ask.

    It is deliberately NOT derived from PREVIEW_BASE_URL. That is the GitHub
    Pages address the deploy writes to, and the whole point of the proxy in
    leadscraper is that a prospect never sees it (api/preview/index.ts).
    """
    base = (public_base or PREVIEW_PUBLIC_BASE or "").strip().rstrip("/")
    if not base or not prospect_id:
        return ""
    return f"{base}/preview/{prospect_id}"


def _json_ld_article(
    headline: str, description: str, canonical_url: str, image_url: str,
    published: str, intel: dict,
) -> str:
    """schema.org Article for the story, with the business as its subject.

    The prospect is paying for a real article on a real publication, and this is
    the machine-readable half of that claim. Every field is dropped when the
    real value is missing rather than filled with a placeholder — a NewsArticle
    claiming an author nobody wrote is the structured-data version of a
    fabricated review.

    Article, not NewsArticle: NewsArticle carries an expectation of reporting,
    and this is a commissioned feature.
    """
    business = {
        "@type": "LocalBusiness",
        "name": intel.get("business_name") or "",
        "url": f"https://{intel['domain']}" if intel.get("domain") else "",
        "telephone": intel.get("phone") or "",
        # `location` is whatever the scrape found: "5006 El Cajon Blvd, San Diego,
        # CA 92115" on one lead and "San Diego, CA" on the next. Both are an
        # address, coarse or exact, so both belong here. It was areaServed until
        # a real build put a street address in it, which reads as the area the
        # bakery serves rather than where it is. Plain text, not a PostalAddress:
        # splitting a one-line string into street/locality/postcode means guessing
        # at the parts, and a wrong structured field is worse than an honest flat
        # one.
        "address": intel.get("location") or "",
    }
    article = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "description": description,
        "image": image_url,
        "datePublished": published,
        "mainEntityOfPage": canonical_url,
        "author": {"@type": "Organization", "name": PUBLISHER_NAME},
        "publisher": {"@type": "Organization", "name": PUBLISHER_NAME},
        "about": {k: v for k, v in business.items() if v},
    }
    article = {k: v for k, v in article.items() if v}

    # "</" cannot appear inside a script element, and a business name is scraped
    # text we do not control. json.dumps escapes quotes; it does not escape this.
    return json.dumps(article, ensure_ascii=False, indent=2).replace("</", "<\\/")


def _seo_head_tags(html: str, intel: dict, canonical_url: str, image_url: str) -> str:
    """The head tags a Sponsored Story is sold on, built from the page itself."""
    headline = _first_tag_text(html, "h1") or _first_tag_text(html, "title")
    description = _meta_description(html, intel)
    published = date.today().isoformat()

    tags = []
    if description:
        tags.append(f'<meta name="description" content="{escape(description, quote=True)}">')
    if canonical_url:
        tags.append(f'<link rel="canonical" href="{escape(canonical_url, quote=True)}">')

    open_graph = [
        ("og:type", "article"),
        ("og:site_name", PUBLISHER_NAME),
        ("og:title", headline),
        ("og:description", description),
        ("og:url", canonical_url),
        ("og:image", image_url),
    ]
    for prop, value in open_graph:
        if value:
            tags.append(f'<meta property="{prop}" content="{escape(value, quote=True)}">')

    # Mirrors Open Graph rather than saying anything new. X reads og:* as a
    # fallback, but LinkedIn and Slack read the twitter:* pair first, and the
    # cost of writing both is four lines.
    twitter = [
        ("twitter:card", "summary_large_image" if image_url else "summary"),
        ("twitter:title", headline),
        ("twitter:description", description),
        ("twitter:image", image_url),
    ]
    for name, value in twitter:
        if value:
            tags.append(f'<meta name="{name}" content="{escape(value, quote=True)}">')

    if headline:
        tags.append(
            '<script type="application/ld+json">\n'
            + _json_ld_article(headline, description, canonical_url, image_url, published, intel)
            + "\n</script>"
        )

    return "\n  ".join(tags)


def _inject_head_tags(html: str, tags: str) -> str:
    """Put the tags inside <head>, or build a head when the model wrote none.

    Before </head> rather than after <head>, so the model's own <title> stays
    the first thing in the document and _inject_base_href's <base> keeps sitting
    where it puts itself.
    """
    if not tags:
        return html
    block = "  " + tags + "\n"
    if "</head>" in html:
        return html.replace("</head>", block + "</head>", 1)
    if "<head>" in html:
        return html.replace("<head>", "<head>\n" + block, 1)
    # No head at all: _close_truncated_html guarantees a </body></html>, not a
    # head, and a page truncated before <head> still deserves its canonical.
    return re.sub(r"(<html\b[^>]*>)", r"\1\n<head>\n" + block.rstrip("\n") + "\n</head>",
                  html, count=1, flags=re.IGNORECASE)


# Words a model reaches for when it stops writing a headline and starts writing
# a label. "Bonjour Patisserie | A Sponsored Story Preview" shipped on a live
# page whose own <h1> read "Inside the Little Italy Patisserie Turning San Diego
# Onto French Pastry"; Su Pan, built on the same code minutes earlier, got it
# right. The title is the line Google prints, so a generic one loses the search
# result the offer is sold on.
_TEMPLATE_TITLE_RE = re.compile(
    r"\b(preview|mock[- ]?up|template|untitled|sponsored story|lead magnet)\b",
    re.IGNORECASE,
)


def _headline_names_the_business(headline: str, name: str) -> bool:
    """Does the headline already say who this is about, in full or in short?

    A whole-string test is too strict, and it shipped: "Prager Brothers Artisan
    Breads" against a headline reading "Inside Carlsbad's Slow-Rise Obsession:
    How Prager Brothers Turned Bread Into a Daily Ritual" is plainly the same
    bakery, named the way a person says it. The strict test said no and the
    title came out as the registered name, a colon, the headline, and its own
    second colon, with "Prager Brothers" in it twice.

    Two words is the shortest form that still identifies a business: "Prager
    Brothers" and "Dark Horse" do, "Su" and "The" do not. A one-word name has no
    short form, so it must appear in full.
    """
    words = name.lower().split()
    if not words:
        return True
    if name.lower() in headline.lower():
        return True
    return len(words) >= 2 and " ".join(words[:2]) in headline.lower()


def _editorial_title(html: str, intel: dict) -> str:
    """A real title for this page, or "" to leave the model's own alone.

    Only replaces a title that reads as a label. Su Pan's "Su Pan Bakery: The
    City Heights Bakery Keeping Tradition Fresh" beats anything assembled
    mechanically, and swapping it out would trade a good title for a consistent
    one. The <h1> is the editorial headline the prompt asked for and the model
    writes it well even when it labels the title, so that is the source.

    Deliberately not truncated. A headline is one unit of meaning and cutting it
    mid-clause reads worse than a long tag; Google shortens the DISPLAY either
    way, and length is not itself an error.
    """
    current = _first_tag_text(html, "title")
    if current and not _TEMPLATE_TITLE_RE.search(current):
        return ""

    headline = _first_tag_text(html, "h1")
    if not headline:
        return ""

    name = (intel.get("business_name") or "").strip()
    if not _headline_names_the_business(headline, name):
        headline = f"{name}: {headline}"
    return f"{headline} | {PUBLISHER_NAME}"


def _replace_title(html: str, title: str) -> str:
    """Swap the document's <title>, or give it one when it has none."""
    if not title:
        return html
    tag = f"<title>{escape(title)}</title>"
    if re.search(r"<title\b[^>]*>.*?</title>", html, re.IGNORECASE | re.DOTALL):
        # A lambda, not a replacement string: a headline containing a backslash
        # or a \1 would otherwise be read as a group reference.
        return re.sub(r"<title\b[^>]*>.*?</title>", lambda _m: tag, html,
                      count=1, flags=re.IGNORECASE | re.DOTALL)
    return _inject_head_tags(html, tag)


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
    # Attribution on the Claim This Site CTA, same helper the two offer magnets
    # use, so all three offer types report clicks the same way (POD01-130).
    booking_url = build_booking_url("smart_site", prospect_id)
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
   "This is a preview of your new website for **{intel['business_name']}**"
   + gold pill "Claim This Site →" button linking to {booking_url}
   Use that URL exactly as written, query string included: the params say which lead and
   which offer the click came from, and a CTA that drops them arrives anonymous.
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

6. NO TESTIMONIALS SECTION. You were given no review text, so there is nothing
   real to build one from. Do not add one, under any heading: not "Testimonials",
   not "What Our Customers Say", not a quote carousel, not a single quoted card.
   See the REVIEWS rule above. The rating and review count, if you were given
   them, belong in the SOCIAL PROOF BAR as bare numbers and nowhere else.

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
    html = _attribute_booking_links(html, booking_url, f"smart site for {intel.get('business_name', '')}")
    _warn_fabricated_reviews(html, "smart site", intel.get("business_name", ""))
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


# Ceiling on the base64 actually WRITTEN INTO one page, which is not the same
# as the bytes downloaded. intel's _MAX_PHOTO_TOTAL_BYTES bounds the unique
# images (2.4 MB raw, ~3.2 MB encoded); this bounds what the page renders, and
# the two diverge the moment a page uses one photo twice.
#
# That is not hypothetical. Pop Pie Co's Get Listed page carried 4 unique images
# in 7 places, paid for 3 of them twice, and came to 5.04 MB against the preview
# proxy's 5 MB cap — 38 KB over, served as a blank "Preview unavailable" with a
# successful build and a `ready` row behind it. The actual markup was 9.6 KB.
#
# 3.5 MB leaves ~1.5 MB of headroom under that cap for the HTML itself.
_MAX_INLINE_RENDER_BYTES = int(os.environ.get("MAX_INLINE_RENDER_BYTES", "3500000"))

# Warn at 4.5 MB, short of the proxy's 5 MB. The budget above should keep every
# page well under this; if one still trips it, the markup itself has grown and
# that is worth knowing BEFORE a prospect opens a blank page.
_PREVIEW_PROXY_WARN_BYTES = int(os.environ.get("PREVIEW_PROXY_WARN_BYTES", "4500000"))


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

    Bounded by _MAX_INLINE_RENDER_BYTES, and in two passes rather than one:
    every image gets its FIRST occurrence inlined before any image gets a
    second. A plain `replace(url, data_uri)` swaps every occurrence, so one
    photo used twice was written into the page twice, and a page could blow the
    proxy's 5 MB cap while the byte budget upstream looked fine. Coverage first
    means a tight budget costs a repeat, never a whole image.

    Occurrences past the budget keep their original URL and hotlink the
    prospect's server, which is the same fallback a photo we could not download
    already takes.
    """
    if not assets:
        return html

    spent = 0

    # Pass 1: one occurrence each, so no image is dropped for a duplicate.
    for url, data_uri in assets.items():
        if url not in html:
            continue
        if spent + len(data_uri) > _MAX_INLINE_RENDER_BYTES:
            continue
        html = html.replace(url, data_uri, 1)
        spent += len(data_uri)

    # Pass 2: whatever budget is left goes on the repeats.
    for url, data_uri in assets.items():
        while url in html and spent + len(data_uri) <= _MAX_INLINE_RENDER_BYTES:
            html = html.replace(url, data_uri, 1)
            spent += len(data_uri)

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
    prospect_id: str = "",
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
    # Every page of a multi-page build carries the same claim bar, so every one
    # of them needs the same attributed CTA (POD01-130).
    booking_url = build_booking_url("smart_site", prospect_id)

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

6. NO TESTIMONIALS SECTION. You were given no review text, so there is nothing
   real to build one from. Do not add one, under any heading. See the REVIEWS
   rule above. The rating and count, if given, are bare numbers in SOCIAL PROOF.

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
   "This is a preview of your new website for **{intel['business_name']}**"
   + gold pill "Claim This Site →" button linking to {booking_url}
   Use that URL exactly as written, query string included: the params say which lead and
   which offer the click came from, and a CTA that drops them arrives anonymous.
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
    html = _attribute_booking_links(html, booking_url, f"smart site page {page['filename']}")
    _warn_fabricated_reviews(html, f"smart site page {page['filename']}", intel.get("business_name", ""))
    html = _inline_photo_assets(html, photo_assets)
    return html


# U+2012 figure dash through U+2015 horizontal bar: every character that reads
# as an em/en dash, not just the two a model reaches for most.
_DASH_CHARS = "‒–—―"

# The same four as HTML entities, named or numeric, hex or decimal. The
# semicolon is optional because models drop it, but then the name must not run
# straight into more letters or digits: a bare ";?" also matched the "&mdash"
# inside "&mdashboard=1" and left "—board=1".
_DASH_ENTITY_RE = re.compile(
    r"&(?:mdash|ndash|#x201[2-5]|#821[0-3])(?:;|(?![A-Za-z0-9]))", re.IGNORECASE
)

# Captures the character before the dash so the swap can see its own context in
# one pass. Rewriting punctuation globally afterwards was the earlier approach
# and it edited text containing no dash at all ("etc., and" lost its comma).
_DASH_SPAN_RE = re.compile(rf"(\S)?\s*[{_DASH_CHARS}]\s*")

# URL-bearing attributes are left alone: a comma and a space inside a URL breaks
# the link. Unreachable on today's pages, which carry only the fixed BOOKING_URL,
# but it becomes reachable the moment a scraped URL lands in one of these. The
# unquoted alternative comes last so a quoted value always wins.
_PROTECTED_ATTR_RE = re.compile(
    r"""\b(?:href|src|srcset)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE
)


def _dash_replacement(match: "re.Match") -> str:
    text, start, end = match.string, match.start(), match.end()
    before = match.group(1) or ""

    if not before:
        return before

    if before == ">":
        # A ">" ends an opening tag or a closing one, and the dash means
        # opposite things either side of that. "<p>— Hello" opened a text node
        # and the dash goes. "<strong>Kickserv</strong> — field service" is a
        # separator: dropping it fused the two words with no space at all.
        tag = text.rfind("<", 0, start)
        if tag != -1 and text.startswith("</", tag):
            return before + ", "
        return before

    # Was the dash trailing? Then a comma renders as a stray one. What ends the
    # run depends on where we are: inside a tag it is the attribute value's
    # closing quote, in body text it is a CLOSING tag. Only a closing one, so
    # "a — <em>b</em>" keeps the separator it needs.
    in_tag = text.rfind("<", 0, start) > text.rfind(">", 0, start)
    trailing = end >= len(text) or (
        text[end] in "\"'" if in_tag else text.startswith("</", end)
    )
    if trailing:
        return before

    if before in ".!?;:,":
        # Punctuation already does the comma's job.
        return before + " "
    return before + ", "


def _swap_dashes(chunk: str) -> str:
    # Entities are decoded here, not before the split, so a "&mdash;" sitting
    # inside a protected href is left alone with everything else in it.
    chunk = _DASH_ENTITY_RE.sub("—", chunk)
    # A dash between digits is a range, not punctuation: "10—20" is "10-20".
    chunk = re.sub(rf"(?<=\d)\s*[{_DASH_CHARS}]\s*(?=\d)", "-", chunk)
    return _DASH_SPAN_RE.sub(_dash_replacement, chunk)


def _strip_em_dashes(html: str) -> str:
    """Enforce the no-em-dash rule the prompt asks for. The prompt asks; a model
    under a long structure spec still slips. Offer magnets only, so the Free
    Website generator's voice is untouched.

    Runs before photo inlining so it never walks a base64 data URI.
    """
    # Rewrite the text, step over every URL attribute value untouched.
    out, last = [], 0
    for attr in _PROTECTED_ATTR_RE.finditer(html):
        out.append(_swap_dashes(html[last:attr.start()]))
        out.append(attr.group(0))
        last = attr.end()
    out.append(_swap_dashes(html[last:]))
    return "".join(out)


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
    # TSD's own funnel names restaurants, coffee shops and retail as the target
    # for Get Listed; without these they fell through to the generic framing.
    "restaurant": (
        "a local restaurant",
        "their menu standouts, the room and the neighborhood, and what regulars keep coming back for",
    ),
    "cafe": (
        "a neighborhood coffee shop",
        "what they roast or pour, the room, and who fills it in the morning",
    ),
    "retail": (
        "a local shop",
        "what they stock, what is hard to find anywhere else, and the street they sit on",
    ),
}


def generate_offer_lead_magnet_page(
    offer: str,
    intel: dict,
    vertical: Optional[str] = None,
    meter=None,
    photo_assets: Optional[dict] = None,
    prospect_id: str = "",
    public_base: str = "",
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

    # Carries which lead, which offer and which page the click came from, so a
    # booked call arrives with context instead of being reconstructed live
    # (POD01-130). prospect_id is optional: run_engine.py and the tests build
    # pages without one, and a magnet with unattributed CTAs still beats a
    # failed build.
    booking_url = build_booking_url(offer, prospect_id)

    # Their real page, not the bare root. A business living inside a larger
    # site shares a domain with its parent, so the root is somebody else's
    # homepage (POD01-34). Falls back to the domain when there is no path,
    # which is every ordinary root-domain lead.
    own_site_url = intel.get("page_url") or f"https://{intel['domain']}"

    if offer == "get_listed":
        role, detail_hint = GET_LISTED_VERTICAL_FRAMING.get(
            vertical, ("a local business", "their services and what makes them worth choosing")
        )
        # TSD's funnel sells "links to your website, menu, reservations and
        # social profiles", which only reads right for food. A contractor's page
        # promising their menu is copy nobody checked.
        links_point = (
            "Website, menu, reservations and socials all linked in one place."
            if vertical in ("restaurant", "cafe")
            else "Your website and socials all linked in one place."
        )
        page_purpose = f"""This is a MOCK-UP of how {intel['business_name']}, {role}, would look
featured in the ThereSanDiego.com business directory, as a personalized preview to close a
Get Listed ($297 one-time, permanent profile) prospect. Build it around {detail_hint}.

STRUCTURE (this is a directory profile mock-up, not a full website):
1. CLAIM BAR: sticky, same as every LVRG preview.
   "This is a preview of your ThereSanDiego.com listing" plus gold pill "Claim This Listing →" linking to {booking_url}
2. PROFILE HEADER: business name, {role} framing, location and neighborhood, primary photo.
   If a rating was supplied above, show it here next to the name in exactly this form: one gold star,
   the rating, then the review count in brackets, e.g. "★ 4.7 (14 reviews)", or "★ 4.7" with no count.
   Never a row of stars: 4.7 drawn as five stars overstates it. Use this same form everywhere the
   rating appears on the page.
3. ABOUT: 4-6 sentences on {detail_hint}, in ThereSanDiego's warm local-guide voice, broken into
   two short paragraphs rather than one block. Work in what they want visitors to do, and speak to
   where they currently struggle, without ever naming the struggle as a criticism of them.
   Link the business name inline to {own_site_url} the first time it appears here.
3b. FEATURE IMAGE: if photos were supplied, place one full-width between ABOUT and WHAT THEY OFFER,
   with a short caption drawn from their real content. Never a stock image, never a placeholder.
4. WHAT THEY OFFER: their real services as a short scannable list, grouped sensibly, two columns on
   desktop. Use only the services given above. If none were listed, omit this section.
   Follow it with a SECOND photo if two or more were supplied.
5. AT A GLANCE: a compact fact panel built ONLY from real data given above.
   Neighborhood, hours, phone, rating, and a "Visit website" link to {own_site_url},
   each shown only if present. Omit the panel entirely if fewer than two of them exist.
   Never write "Not listed" on the page.
5b. LINKS BACK TO THEIR SITE: "links to your website, menu, reservations and social profiles" is
   one of the things the $297 profile is sold on, so the mockup has to demonstrate it. Carry at
   least TWO links to {own_site_url}: the inline one in ABOUT and the fact-panel one.
   Use their real domain exactly as given, never a placeholder. If socials were supplied, link
   those in the footer. Never add rel="nofollow".
6. WHY LIST HERE: short points, every one of them confirmed on TSD's own funnel.
   Permanent page, no monthly fee, no expiration. Live within 5 business days.
   SEO-optimized so San Diegans searching for what you offer find you.
   {links_point}
   Send us your photos and we format and publish them for you.
   Person and business schema, so Google is told who you are as the person behind the business.
7. WHERE THIS SITS: one short line placing the profile in context, that it lives on a
   local guide 70,000+ San Diegans read every month, not on a pay-to-play directory.
8. GALLERY: real photos if provided, otherwise omit.
9. SOCIAL PROOF: if a rating and review count were supplied above, show them as a stat in the same
   one-star form as the header, and
   nothing more than the stat. Re-read the REVIEWS rule above before writing this section: you
   were given no review text, so this section is two numbers, not a sentence about what anyone said.
   If no rating, omit this section.
10. CTA: "Claim this listing for $297, one time, permanent. Live in 5 business days."
   plus a quieter second line: "The $297 comes off your first Sponsored Story if you upgrade later."
   Both drive to {booking_url}
11. FOOTER: location, phone, hours."""
    elif offer == "sponsored_story":
        # Do not price this off First Look. First Look ($197 one-time) is a SOCIAL
        # plan, an Instagram post rather than an article, and sits on /social-plans.
        # Sponsored Stories are monthly and start at $497. Verified 7 Sep 2026.
        page_purpose = f"""This is a MOCK-UP of a Sponsored Story: a short editorial feature as it would
run on ThereSanDiego.com and get promoted to their 70,000+ monthly audience, built to close an
Advertising prospect on a Sponsored Story plan, of which LOCAL at $497/month is the entry tier.

STRUCTURE (this is an editorial feature mock-up, not a full website):
1. CLAIM BAR: sticky, same as every LVRG preview.
   "This is a preview of your Sponsored Story" plus gold pill "Claim This Story →" linking to {booking_url}
2. ARTICLE HEADER: a real editorial-style headline about {intel['business_name']}, never a generic
   "About Us" title. Byline "There San Diego Staff", a dateline reading "San Diego", hero photo if provided.
3. THE STORY: 7-9 paragraphs in ThereSanDiego's warm, locals-know-locals editorial voice, using their
   REAL description, services and neighborhood. It should read like a feature a San Diegan would
   actually enjoy, not an ad. Break it up so it scans like a magazine piece rather than a wall of text:
   - Two or three SUBHEADINGS between sections, written as real editorial lines, never "About" or "Services"
   - One PULL QUOTE styled large and set apart. It must be drawn from THEIR OWN description or services,
     never attributed to a customer and never in quotation marks as if someone said it
   - Photos placed BETWEEN paragraphs, not all stacked at the top. See the photo rule below.
   Cover, in this order: what the place is and where it sits, the story of how it came to be if their
   own content supports it, what they actually do best drawn from their real services, what makes it
   worth the trip, what a first-time visitor should do, and a closing beat built on what they want
   visitors to do. Never pad: if their content does not support nine paragraphs, write seven good ones
   rather than nine with filler.
4. THE DETAILS: a short editorial fact box beside or under the story, built ONLY from real data above.
   Neighborhood, hours, phone, rating, and a link to their website, each only if present. Omit the box
   if fewer than two exist. Never print "Not listed" or an empty row.
5. SOCIAL PROOF: if a rating and review count were supplied above, work the stat into the story or the
   fact box. The stat is the whole of it. Re-read the REVIEWS rule above and apply it here AND in the
   pull quote AND anywhere in the article body, because this is the section that has broken before.
   The failure was not a fake testimonial with a name under it. It was a line in the middle of the
   story reading "Yelp reviewers have specifically called out ..." on a page with no review text
   behind it. An editorial voice makes that sentence easy to write and no less invented.
   The pull quote is THEIR OWN words about themselves, set large and WITHOUT quotation
   marks, exactly as item 3 says. Set large is what makes it a pull quote. Quotation
   marks would make it look like someone said it to a reporter, and nobody did.
   This page carries no quoted speech at all.
   If no rating, omit this section.
5b. LINKS BACK TO THEIR SITE: this is not decoration, it is the product. A Sponsored Story is sold on
   "published on ThereSanDiego.com with links back to your website", and the SEO value of the placement
   IS those links. The page must carry at least THREE, all pointing at https://{intel['domain']}:
   - the business name in the FIRST paragraph, linked inline
   - one contextual link mid-article on a real phrase, for example their signature service or menu
   - one in the fact box or the closing paragraph, reading as a plain invitation to visit their site
   Use their real domain exactly as given. Style them as normal editorial links, underlined or coloured,
   never as buttons. Never add rel="nofollow": the whole point of the placement is that the link counts.
   If their socials were supplied, link those too, in the footer only.
6. GUARANTEE CALLOUT: "Every Sponsored Story comes with guaranteed impressions. If we don't hit the number, we keep promoting until we do."
7. REACH: a short stat strip using ThereSanDiego's real audience numbers.
   70,000+ monthly visitors, 700,000+ monthly reach, 25,000+ newsletter subscribers,
   80,000+ social followers across Facebook and Instagram. Do not inflate or invent any of these.
   Carry the "+" where it is written above. These are TSD's own published figures and the "+"
   is part of them: 80,000+ is what the client's rep guardrails say to hold to, and dropping it
   turns a floor into an exact count we did not measure.
8. PLANS: the three real tiers, as three simple cards. State the monthly price plainly.
   These are MONTHLY plans and must never be shown as a one-time fee.
   - LOCAL, $497/month, 10,000 guaranteed impressions a month, neighborhood targeting, one story per quarter
   - CITYWIDE, $997/month, 25,000 guaranteed impressions a month, the full San Diego metro, one story per month
   - COUNTYWIDE, $1,500/month, 50,000 guaranteed impressions a month, all of San Diego County, one story per month
   Under the cards, one line: "Every plan includes ad campaign management and geo, age and demographic targeting. Organic impressions are never charged."
9. CTA: driving to {booking_url}
10. FOOTER: location, phone, hours."""
    else:
        raise ValueError(f"Unknown offer for generate_offer_lead_magnet_page: {offer!r}")

    # The Sponsored Story is sold on its backlinks, so it carries one more than
    # the profile does.
    min_own_links = 3 if offer == "sponsored_story" else 2


    page_prompt = f"""You are building a personalized lead-magnet PREVIEW PAGE for {intel['business_name']}.
This is NOT a full business website. See the specific structure below for what it actually is.

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
- What they want visitors to do: {intel.get('cta_angle') or 'Get in touch'}
- Where they currently struggle: {_pain_point_context(intel, None) or 'Not identified'}
- Brand vibe: {intel.get('brand_vibe', 'clean, modern')}
- Primary color: {intel.get('primary_color', '#333')}

━━━ REAL CONTENT ━━━
{photo_block}

{reviews_block}

{press_block}

{social_block}

━━━ TECH STACK ━━━
Use Tailwind CSS via CDN. Include this in <head>:
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: '{intel.get('primary_color','#f59e0b')}' }} }} }} }}</script>
Use Google Fonts matching the brand vibe above.
NO inline style= attributes. Use Tailwind classes exclusively.

━━━ REQUIRED LINKS ━━━
Two different destinations. Do not confuse them, and do not let one stand in for the other.

1. THE BUSINESS'S OWN WEBSITE: {own_site_url}
   This is the one that was missing, and it is the one being sold. A Sponsored Story is bought
   for "links back to your website"; a Get Listed profile is bought for "links to your website,
   menu, reservations and social profiles". A page that names the business and never links to it
   has failed to demonstrate the product.
   MINIMUM {min_own_links} links to this URL, in the positions named below.
   Use it VERBATIM, including any path. The path is often what separates this business from
   whoever owns the root domain, so trimming it can point the prospect at a different company.
   Never example.com, never "#", never a link to ThereSanDiego instead.
   Style them as ordinary editorial links. Never rel="nofollow": the link counting is the point.

2. THE BOOKING PAGE: {booking_url}
   For the claim bar, the CTA buttons and the plan cards ONLY. This is OUR link, not theirs.
   It must never replace a link to their own website, and adding more of these does not satisfy
   the requirement above.
   Use this URL EXACTLY as written, query string included. The params after the "?" say which
   lead and which offer the click came from, and a CTA that drops them arrives anonymous.
   Do not shorten it, do not strip it back to the bare domain, do not vary it between buttons.

Socials, if supplied, go in the footer. They are additional, not a substitute for the website link.

━━━ WHAT TO BUILD ━━━
{page_purpose}

━━━ COPY RULES ━━━
- Reference {intel.get('neighborhood') or intel.get('location','').split(',')[0]} naturally
- NEVER write fake testimonials. If no real reviews, skip quotes entirely
- NEVER invent pricing beyond what's given above
- Single page: no nav bar and no links to OTHER PAGES OF THIS MOCKUP, because there are none.
  This does NOT mean avoid links. Outbound links to the business's own website are REQUIRED,
  see the REQUIRED LINKS block above
- NO EM-DASHES anywhere in the copy. Do not use the character "—" or "–". Use a full stop,
  a comma, or a colon instead. This applies to headings, body copy, captions and buttons.
- Avoid the AI tells: "elevate", "unlock", "seamless", "in today's world", "nestled",
  "whether you're ... or ...", "it's not just X, it's Y". Write the way a local writer would
- Only state facts given in the intel above. If a detail is missing, leave it out rather than
  filling the gap with a plausible guess
- LINK TO {own_site_url} using their real URL, never example.com and never "#".
  The links are the product here, not decoration: a Sponsored Story is sold on the backlinks it
  carries, and the Get Listed profile is sold on gathering every link in one place. A page that
  mentions the business and never links to it has failed to demonstrate the thing being bought
- PHOTO PLACEMENT: spread the supplied photos through the page, one after the header and the rest
  BETWEEN sections or paragraphs. Never stack them all at the top and never end on a photo wall.
  Use ONLY the photo URLs given above, in the order given. If none were supplied, write the page
  without images and do NOT substitute stock photography, an illustration, a solid colour block or
  an emoji standing in for a picture. A clean page with no image beats an obvious placeholder on a
  mockup carrying the prospect's own branding
- USE EACH PHOTO AT MOST ONCE on the page. Never repeat one to fill a second slot, in an <img>, a
  CSS background or anywhere else. If there are fewer photos than places you had planned to put
  one, use fewer images. A page showing the same picture twice looks like a template, and the
  bytes are paid for twice: four images used in seven places produced a 5 MB page that the preview
  proxy refused to serve at all

━━━ OUTPUT ━━━
Return ONLY the complete HTML. No explanation. No markdown fences. No chat widget (injected separately).
Start with <!DOCTYPE html>"""

    client = _get_client(max_retries=PAGE_GENERATION_MAX_RETRIES)
    with client.messages.stream(
        model="claude-sonnet-5",
        thinking=NO_THINKING,
        max_tokens=OFFER_PAGE_MAX_TOKENS,
        messages=[{"role": "user", "content": page_prompt}],
    ) as stream:
        response = stream.get_final_message()
    cost.record(meter, f"offer_page:{offer}", "claude-sonnet-5", response)

    if response.stop_reason == "max_tokens":
        print(f"  [generator] WARNING: hit max_tokens ({OFFER_PAGE_MAX_TOKENS}) on a {offer} lead magnet for {intel['business_name']} — may be cut short")

    html = first_text(response).strip()
    html = _strip_markdown_fences(html)
    html = _close_truncated_html(html)
    html = _strip_em_dashes(html)
    html = _attribute_booking_links(html, booking_url, f"{offer} page for {intel['business_name']}")

    # Count the backlinks BEFORE photo inlining, so a base64 blob containing the
    # domain by coincidence cannot inflate the number.
    #
    # The prompt asks for these and the first version that asked was ignored
    # completely: the page carried six links, every one of them ours, and none
    # to the business being sold. Asking is not the same as knowing, so this
    # reports what actually shipped. A warning rather than a raise: a page with
    # too few links is still a usable mockup, and failing the build would cost
    # the user a generation over something a rebuild may fix.
    own_links = _count_own_domain_links(html, intel.get("domain", ""))
    if own_links < min_own_links:
        print(
            f"  [generator] WARNING: {offer} page for {intel['business_name']} carries "
            f"{own_links} link(s) to {intel.get('domain')!r}, expected at least "
            f"{min_own_links}. The backlink IS the product on this offer."
        )

    _warn_fabricated_reviews(html, offer, intel.get("business_name", ""))

    # Read before inlining. After it this src is a data: URI, and og:image has
    # to be an address a crawler can go and fetch.
    hero_image = _first_remote_image(html)

    html = _inline_photo_assets(html, photo_assets)

    # The article half of what a Sponsored Story is sold as (POD01-126). Get
    # Listed is a directory profile rather than a published article and its own
    # ticket asks for none of this, so it stays a single-offer concern until
    # someone decides otherwise.
    if offer == "sponsored_story":
        # Before the head tags, so og:title and the JSON-LD headline read the
        # same <h1> this may have just promoted into the title.
        html = _replace_title(html, _editorial_title(html, intel))
        html = _inject_head_tags(html, _seo_head_tags(
            html, intel,
            canonical_url=preview_page_url(public_base, prospect_id),
            image_url=hero_image,
        ))

    # The proxy in leadscraper (api/preview/index.ts) refuses anything over
    # 5 MB with a 502, and the prospect gets a blank "Preview unavailable" while
    # the build reports success and the row says `ready`. Nothing else in this
    # chain notices, so say it here where the bytes are known.
    page_bytes = len(html.encode("utf-8"))
    if page_bytes > _PREVIEW_PROXY_WARN_BYTES:
        print(
            f"  [generator] WARNING: {offer} page for {intel['business_name']} is "
            f"{page_bytes / 1048576:.2f} MB. The preview proxy rejects anything over 5 MB "
            f"and serves a blank page, so this may not be viewable."
        )

    return html


def _count_own_domain_links(html: str, domain: str) -> int:
    """How many hrefs point at the prospect's OWN site.

    Only href targets count. The business name appears in body copy on every one
    of these pages, and a substring search over the whole document would report
    a page rich in backlinks when it has none, which is the exact failure this
    is here to catch.

    Socials are excluded deliberately: an Instagram profile is not the website
    the offer is sold on, and counting it let a page satisfy the requirement
    while still never linking to the business itself.
    """
    if not domain:
        return 0
    host = canonical_domain(domain)
    if not host:
        return 0
    hrefs = re.findall(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    return sum(1 for h in hrefs if canonical_domain(h) == host)


# Nouns that stand in for "someone who left a review". The Pop Pie Co page used
# "Yelp reviewers"; the prompt at the time banned only a named customer, so it
# broke nothing. Platforms are listed by name because that is what the model
# reached for, and it reads as more credible than an unnamed group, not less.
# \b at both ends is load-bearing, not tidiness: "preview" contains "review",
# and the claim bar on EVERY page generated here reads "This is a preview of
# your ...". Without the boundaries this warns on every build, which is the
# same as not warning at all.
_REVIEW_SOURCE = (
    r"\b(?:reviewers?|reviews?|testimonials?|yelp|tripadvisor|trip\s+advisor|"
    r"google\s+(?:reviews?|ratings?)|regulars|patrons|diners|customers|"
    r"guests|visitors|locals)\b"
)
# The subset that is a claim about EVIDENCE rather than a way of saying "people".
#
# The distinction earns its place on the quoted branch. The Sponsored Story asks
# for a pull quote drawn from the business's own copy, in a voice the same prompt
# calls "locals-know-locals" — so a sanctioned pull quote reading "...for the
# locals who kept asking us to open one." is quote marks plus a soft noun, and
# treating that as enough warns on output the prompt just requested. Reviewers
# and platforms are different: there is no innocent reason for those to appear
# beside a quotation on a page that was handed no review text.
_REVIEW_SOURCE_STRICT = (
    r"\b(?:reviewers?|reviews?|testimonials?|yelp|tripadvisor|trip\s+advisor|"
    r"google\s+(?:reviews?|ratings?))\b"
)
# Reporting verbs: the sentence claims to know what those people SAID.
#
# Deliberately excludes the feeling verbs — love, adore, swear by is borderline
# and stays out. "A spot locals love" is how every city guide on earth writes
# and it claims no evidence; "locals say it is the best" claims evidence. That
# line is what keeps this warning readable: fire on puffery and every build
# warns, and a warning that always fires is one nobody reads.
_REVIEW_REPORTING = (
    r"\b(?:call(?:s|ed|ing)?\s+(?:it|them|the\w*)?\s*out|prais\w+|rave\w*|"
    r"mention\w*|describ\w+|says?|said|note[sd]?|agree\w*|report\w*|"
    r"laud\w+|singl\w+\s+out|point\w*\s+to)\b"
)


# A testimonials section by any name. _build_reviews_block tells the model not
# to add one at all, so the heading is the finding: there is no real review text
# on this build for it to have been filled from.
_TESTIMONIAL_HEADING = (
    r"\btestimonials?\b"
    r"|\bwhat\s+(?:our\s+)?\w+\s+(?:are\s+)?say(?:ing)?\b"
    r"|\bwhat\s+people\s+are\s+saying\b"
)
# The testimonial byline convention: "Marcus R.", "Marcus R., San Diego, CA",
# "Sarah, La Jolla".
#
# The trailing guard is (?![a-zA-Z]), not \s: block ends are rewritten to ". "
# above, so a byline arrives as "Marcus R.." and requiring whitespace or a comma
# after the initial missed all three testimonials on the one page that has them.
_TESTIMONIAL_BYLINE = (
    r"[A-Z][a-z]+\s+[A-Z]\.(?![a-zA-Z])"                    # Marcus R.
    r"|[A-Z][a-z]+\s*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*,\s*[A-Z]{2}\b"  # Ana, San Diego, CA
)

# `Word, City, ST` is also what an ADDRESS looks like, and what the Sponsored
# Story's own dateline looks like. The prompt mandates a byline of "There San
# Diego Staff" and a dateline of "San Diego"; a Get Listed profile is built
# around the business's address. Neither is a testimonial, and warning on either
# means warning on content the prompt requires.
#
# So a byline match is discarded when the surrounding text shows it is the
# publication's own or the business's own.
_NOT_A_PERSON = re.compile(r"\bstaff\b|\bby\s+there\b|\bteam\b|\beditor", re.IGNORECASE)

# Boilerplate the prompt writes in quotation marks and every page carries. These
# are 41 to 51 characters, so they clear the 40-char floor for a candidate quote
# and would pair with any address later in the header.
_CLAIM_BAR_QUOTE = re.compile(
    r"^\s*(?:this is a preview of|this site was built for)", re.IGNORECASE
)


def _warn_fabricated_reviews(html: str, label: str, business_name: str) -> list:
    """Report any invented review evidence on a finished page. Returns the hits.

    Called from all three generators, before photo inlining so a base64 blob
    cannot match and a 5 MB string is not walked sentence by sentence.

    Smart Site needs this as much as the two magnets do, and arguably more: the
    fabricated testimonials that prompted the check were found on a Smart Site
    page in output/sites, not on a lead magnet. Three invented customers with
    names and cities, on a page carrying a real business's branding.
    """
    hits = _find_attributed_review_claims(html, business_name)
    if hits:
        print(
            f"  [generator] WARNING: {label} page for {business_name} reports what reviewers "
            f"said, on a page given no review text. This is fabricated evidence on a page "
            f"about a real business, sent to that business. Read these before it goes out:"
        )
        for hit in hits[:5]:
            print(f"  [generator]   > {hit[:200]}")
        if len(hits) > 5:
            print(f"  [generator]   ... and {len(hits) - 5} more")
    return hits


def _find_attributed_review_claims(html: str, business_name: str = "") -> list:
    """Invented review evidence on a page that was given no review text.

    `business_name` is optional and only sharpens shape 3: a Get Listed profile
    prints the business's own address, which has the same `Word, City, ST` shape
    as a testimonial byline. Knowing the name separates "Pop Pie Co, La Jolla,
    CA" (a location) from "Marcus R., San Diego, CA" (a person vouching).

    Every generated page is given a star rating and a count and nothing else
    (see _build_reviews_block). So anything on the finished page claiming to
    know what a reviewer actually SAID was invented, and this returns it.

    A warning rather than a raise, matching _count_own_domain_links: this fires
    on a whole generation and a page is still a usable mockup with one bad line
    in it. The point is that the line gets SEEN before the page is emailed to
    the business it invents a quote about, which is what did not happen on
    POD01-133 — the prompt said not to, the model did it anyway, and nothing
    between the model and the prospect's inbox looked.

    Three shapes, because the fabrication takes three and the first pass here
    only caught one:

    1. A sentence naming who it is quoting. "Yelp reviewers have specifically
       called out ..." — the POD01-133 line. Paraphrase counts: "Reviewers
       praise the pastries" is the same claim with the punctuation removed.
    2. A testimonials SECTION. The prompt says never build one, because there is
       nothing real to fill it with, so the heading alone is the finding.
    3. A quoted passage with a person's byline under it, which is how the model
       actually builds a testimonial. Found on a page already on disk here:

           "Knowing that every sip supports their carbon-negative mission ..."
           Marcus R., San Diego, CA

       Shape 1 cannot see this one. The quote and the name it is attributed to
       sit in sibling elements, so no single sentence contains both, which is
       exactly why this needs its own pass rather than a wider sentence regex.
    """
    # `\Z` as an alternative closer, because an UNCLOSED <style> is a real state
    # here, not a hypothetical: a response that hit max_tokens gets stapled shut
    # by _close_truncated_html, which appends </body></html> and does not close
    # a style block. Matching only on </style> then strips nothing, and every
    # CSS class name (.testimonial-card, .testimonials-grid) reaches the scan as
    # if it were page copy. Measured on the pages in output/sites: 8 to 13
    # "findings" per page, all of them stylesheet.
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?(?:</\1\s*>|\Z)", " ", html)
    # A closing block tag ends a sentence. Without this the page collapses into
    # a handful of enormous pseudo-sentences (a nav strip, a whole section) that
    # the length guard below then skips, so a real fabricated quote sitting in
    # the same <section> as a card grid is never even examined. It also decides
    # what the warning PRINTS: the offending sentence, not 400 characters of
    # surrounding menu text.
    text = re.sub(
        r"(?i)</(p|div|section|article|h[1-6]|li|td|th|blockquote|figcaption|"
        r"span|em|strong|cite)\s*>",
        ". ",
        text,
    )
    text = re.sub(r"(?i)<br\s*/?>", ". ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (
        ("&quot;", '"'), ("&ldquo;", '"'), ("&rdquo;", '"'),
        ("&#34;", '"'), ("&#39;", "'"), ("&rsquo;", "'"), ("&amp;", "&"),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"\s+", " ", text)

    hits = []

    # 1. Sentences that report what someone said.
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(sentence) > 400:
            continue
        if not re.search(_REVIEW_SOURCE, sentence, re.IGNORECASE):
            continue
        quoted = re.search(r"[\"“”'‘’]", sentence) is not None
        reporting = re.search(_REVIEW_REPORTING, sentence, re.IGNORECASE) is not None
        strict = re.search(_REVIEW_SOURCE_STRICT, sentence, re.IGNORECASE) is not None

        # A reporting verb is a claim about what someone said whoever they are,
        # so any source noun counts there ("Diners say the service is quick").
        # Quote marks ALONE only count beside a reviewer or a platform, or the
        # sanctioned pull quote trips it (see _REVIEW_SOURCE_STRICT).
        if reporting or (quoted and strict):
            hits.append(sentence.strip())

    # 2. A testimonials section, by heading. "What Our Guests Say" is the same
    #    section wearing an editorial hat, so match the phrasing too.
    #
    #    One report per section: the real page reads "Testimonials. What
    #    Adventurers Say." and matches twice, three words apart, for what is one
    #    finding. Skip a match that lands inside the window already reported.
    reported_to = -1
    for match in re.finditer(_TESTIMONIAL_HEADING, text, re.IGNORECASE):
        if match.start() < reported_to:
            continue
        reported_to = match.start() + 160
        hits.append(f"[testimonials section] {text[match.start():reported_to].strip()}")

    # 3. A quoted passage with a person's byline under it.
    #
    #    The byline pattern is deliberately the testimonial convention and not
    #    "any capitalised word": a first name plus a surname initial ("Marcus
    #    R."), or a name followed by a city and state. A business name alone
    #    does not match, which keeps the sanctioned pull quote (the business's
    #    OWN words, which the Sponsored Story prompt asks for) out of the
    #    results.
    for match in re.finditer(r"[\"“]([^\"“”]{40,400})[\"”]", text):
        quote = match.group(1).strip()
        # The claim bar is quoted boilerplate on every page. Pairing it with an
        # address further down the header produced a hit on three clean pages.
        if _CLAIM_BAR_QUOTE.match(quote):
            continue
        # 60, not 120. A testimonial byline sits directly under its quote — on
        # the real page it starts 14 characters after it. The wider window
        # bought nothing and let a quote pair with an address elsewhere in the
        # layout.
        following = text[match.end():match.end() + 60]
        if not re.search(_TESTIMONIAL_BYLINE, following):
            continue
        # The publication's own byline, or the business's own name beside its
        # own address. Both are locations and mastheads, not people vouching.
        if _NOT_A_PERSON.search(following):
            continue

        # The block-tag rewrite leaves ". . ." runs between the card's
        # sibling elements. Collapse them so the warning reads as a name.
        byline = re.sub(r"^[\s.,-]+", "", following)
        byline = re.sub(r"(?:\.\s*){2,}", ". ", byline).strip(" ,")

        # startswith, not "in". An address line reads "<Business>, <City>, ST",
        # so the name LEADS it. A substring test also matched the business name
        # anywhere in the byline, and San Diego place names are ordinary
        # business names here: for a business called "Vista" or "Diego", the
        # byline "Marcus R., San Diego, CA" contains the name and a real
        # fabricated testimonial was silently dropped. That fails in the
        # dangerous direction, a missed invention rather than a false alarm.
        if business_name and byline.lower().startswith(business_name.lower()):
            continue
        # 120, not the full quote: the caller prints 200 characters per hit and
        # the NAME is the half that proves it was fabricated, so it must survive
        # the truncation.
        hits.append(f'"{quote[:120]}..." attributed to "{byline[:50].strip(" ,")}"')

    # A page can trip more than one shape on the same testimonial (the section
    # heading AND the byline under it). Dedupe on the text so the warning lists
    # findings, not the same finding twice.
    seen, unique = set(), []
    for hit in hits:
        if hit not in seen:
            seen.add(hit)
            unique.append(hit)
    return unique


def build_offer_page_site(
    offer: str,
    intel: dict,
    prospect_id: str,
    vertical: Optional[str] = None,
    meter=None,
    photo_assets: Optional[dict] = None,
    public_base: str = "",
) -> str:
    """Generate ONE offer mockup, inject the chat widget, write it to disk, and
    return the folder path.

    Exists so api.py can treat an offer page exactly like generate_site's
    return value and hand it straight to deploy_site. generate_offer_lead_magnet_page
    deliberately returns a bare HTML string (its prompt says the widget is
    "injected separately"), which is the right shape for a caller that wants
    the markup and the wrong shape for the deploy path.

    The widget injection is the same two lines generate_site uses. Keep them in
    step: a preview that renders without the chat widget is a preview the
    prospect cannot reply from.
    """
    print(f"  [generator] Generating {offer} mockup for {intel['business_name']}...")

    html = generate_offer_lead_magnet_page(
        offer, intel, vertical=vertical, meter=meter, photo_assets=photo_assets,
        prospect_id=prospect_id, public_base=public_base,
    )

    widget_html = _build_chat_widget(intel)
    if "</body>" in html:
        html = html.replace("</body>", widget_html + "\n</body>")
    else:
        html += widget_html

    site_dir = os.path.join(SITES_DIR, prospect_id)
    os.makedirs(site_dir, exist_ok=True)
    index_path = os.path.join(site_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  [generator] ✓ {offer} mockup saved to {site_dir}")
    return site_dir


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
                             meter=meter, photo_assets=photo_assets,
                             prospect_id=prospect_id)
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
