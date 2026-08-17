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
import threading
import time
import re
import anthropic
from html import unescape as _unescape
from urllib.parse import urljoin, urlparse, parse_qs
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

# A page yielding less text than this was not meaningfully read, whatever the
# status code said. The guard this backs used to test `if not raw_text`, which
# only catches a BLANK response — so an HTTP 401 body ("Private Site", 19 chars)
# and a parked "Coming Soon!" page (12 chars) both passed as prospect content
# and the model invented the whole business from them. Real homepages clear this
# by two orders of magnitude; the smallest healthy site measured was 16,301.
MIN_SITE_TEXT_CHARS = int(os.environ.get("MIN_SITE_TEXT_CHARS", "200"))

# --- Playwright rendering ----------------------------------------------------
#
# The fallback below Firecrawl used to be a bare `requests.get`, which returns
# the page as SHIPPED, not as RENDERED. On a Next.js or SPA prospect that is an
# empty shell: measured on 149 real lead domains, 11 came back under the
# MIN_SITE_TEXT_CHARS floor on that path (texascreative.com — 17 chars,
# cafepascale.com — 19) and refused to build at all, and another 19 read fine
# but yielded ZERO photos. Rendering the page in a real browser is the only way
# to see what the prospect's own customers see.
#
# Off by one env var, because this runs inside a hard build deadline and a
# browser is the heaviest thing in the path. PLAYWRIGHT_ENABLED=0 falls straight
# back to the old fetch and the engine keeps working.
PLAYWRIGHT_ENABLED = os.environ.get("PLAYWRIGHT_ENABLED", "1").strip().lower() \
    not in ("0", "false", "no", "off", "")

# Per-page ceiling for the whole render (navigate + settle + lazy-image pass).
# 20s is the largest number that still leaves the fetch fallback room inside
# SCRAPE_BUDGET_SECONDS; the six benchmark sites all finished in under 12s.
PLAYWRIGHT_TIMEOUT_MS = int(os.environ.get("PLAYWRIGHT_TIMEOUT_MS", "20000"))

# Whole-scrape ceiling, across every tier. Without it the tiers ADD UP:
# Firecrawl 25s + Playwright 20s + fetch 15s = 60s, and generation alone needs
# ~82-95s of the caller's 140s (build-smart-site ENGINE_TIMEOUT_MS, raised from
# 135s on 12 Aug — most of the older comments in this file still say 135). That
# combination times out the build — the one outcome a fallback chain is supposed
# to prevent.
#
# 35 is picked so that adding a whole browser to the chain does not cost the
# build a single second of worst case. Before this change the worst path was
# Firecrawl 25s + fetch 15s = 40s. With the budget at 35 the new worst path is
# Firecrawl 25s, then Playwright capped by what remains (10s), then the fetch at
# its 5s floor — 40s again, with the browser fitted inside the old ceiling
# rather than bolted on after it. The typical path is far cheaper: Firecrawl
# answered in 1.9-4.5s across the six benchmark sites and a render adds ~7-10s
# only when it is actually needed.
SCRAPE_BUDGET_SECONDS = int(os.environ.get("SCRAPE_BUDGET_SECONDS", "35"))

# Below this much remaining budget, starting a browser cannot finish, so the
# tier is skipped rather than started and abandoned. Chromium cold start alone
# is ~1-2s before the first byte of navigation.
_PLAYWRIGHT_MIN_SECONDS = 6

# Time to let triggered lazy-loaded images actually fetch after scrolling.
_PLAYWRIGHT_SETTLE_MS = 1200

# Set to launch a chromium the image already ships instead of the one Playwright
# downloads. Left empty, Playwright uses its own. See nixpacks.toml.
PLAYWRIGHT_CHROMIUM_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()

# api.py runs every build's intel stage on the default thread pool
# (`loop.run_in_executor(None, scrape_site, domain)`), so N concurrent builds
# mean N concurrent browsers. Chromium is ~300MB resident each; unbounded, three
# simultaneous prospects can OOM the container and take down builds that had
# nothing to do with rendering. Requests that cannot get a slot fall through to
# the fetch tier, which is slower-but-worse output rather than no output.
#
# Floored at 1: BoundedSemaphore raises on a negative value, and it is raised at
# IMPORT time, so `PLAYWRIGHT_MAX_CONCURRENT=-1` would not degrade the scrape —
# it would stop the engine booting at all. Use PLAYWRIGHT_ENABLED=0 to turn the
# tier off; this knob only sizes it.
PLAYWRIGHT_MAX_CONCURRENT = max(1, int(os.environ.get("PLAYWRIGHT_MAX_CONCURRENT", "2")))
_PLAYWRIGHT_SLOTS = threading.BoundedSemaphore(PLAYWRIGHT_MAX_CONCURRENT)

# Filenames/paths that are almost never a photo of the business.
#
# Token-bounded, not a bare substring. Unanchored, these words also matched
# inside ordinary ones — /images/iconic-dental.jpg lost to "icon",
# /photos/badger-team.jpg to "badge", /media/arrowhead-plaza.jpg to "arrow" and
# /gallery/blankenship.jpg to "blank". Stripping the HOST fixed the domain-level
# case (badgerroofing.com); this fixes the same defect one level down, in the
# path, where it was still silently costing prospects their photos.
#
# `s?` keeps the plurals working: /logos/hero.jpg and /badges/x.png are still
# junk, while /iconic-…, /badger-… and /arrowhead-… are not.
_JUNK_IMAGE = re.compile(
    r"(?<![a-z0-9])"
    r"(logo|icon|favicon|sprite|badge|avatar|placeholder|pixel|tracking|"
    r"spinner|loader|arrow|chevron|bullet|divider|pattern|1x1|blank)"
    r"s?(?![a-z0-9])",
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


def _sans_host(url: str) -> str:
    """Path + query — everything except the HOST.

    Scope for the unanchored substring filters (_JUNK_IMAGE, _PROMO_IMAGE).
    Running those against the full URL let a prospect's own domain match:
    blankslatecoffee.com hit "blank", badgerroofing.com hit "badge", and the
    business lost every candidate photo to its own name.

    The host is the whole defect, so the host is all that comes off. Dropping
    the QUERY too would blind the filters to CDNs that carry the filename there
    (`/cdn?file=logo.png`) — a different silent failure on a different set of
    sites, which is exactly the trade _PHOTO_EXT already refuses to make.

    Not for the $-anchored checks (extension, _WP_THUMB): those must see a
    string that ENDS at the filename, so they keep using the bare path.
    """
    parts = urlparse(url)
    return parts.path + (("?" + parts.query) if parts.query else "")


# Framework image optimisers serve every photo through a proxy endpoint and put
# the real URL, percent-encoded, in a query parameter:
#
#   /_next/image?url=https%3A%2F%2Fapi.example.com%2Fhero.png&w=3840&q=75
#
# Nothing downstream can read that. _PHOTO_EXT needs the extension at the end of
# the string or before a `?`, and here it is followed by `&w=`; _JUNK_IMAGE sees
# an opaque `/\_next/image` path and cannot tell a hero shot from a logo. So the
# whole page scores as photo-less. texascreative.com serves all 32 of its images
# this way — rendering the page found them and the filter then dropped every one.
_PROXIED_IMAGE_PARAMS = ("url", "src", "image", "imageurl", "source")


def _unwrap_image_proxy(url: str) -> str:
    """The real image URL behind an optimiser endpoint, or `url` unchanged.

    Deliberately conservative: only unwraps when the parameter decodes to
    something that is unmistakably a URL of its own (absolute http(s), or a root
    path), so an ordinary `?url=summer-sale` cannot rewrite a legitimate src.
    """
    parts = urlparse(url)
    if not parts.query:
        return url
    try:
        params = parse_qs(parts.query)
    except Exception:
        return url
    for name in _PROXIED_IMAGE_PARAMS:
        for key in (name, name.upper()):
            values = params.get(key) or []
            inner = (values[0] if values else "").strip()
            if not inner:
                continue
            if inner.startswith(("http://", "https://")):
                return inner
            if inner.startswith("/"):
                # Same-host proxy: /_next/image?url=/uploads/hero.jpg
                return urljoin(f"{parts.scheme}://{parts.netloc}", inner)
    return url


def _photo_score(url: str) -> int:
    """Rank candidates so the hero is a photo, not a promo banner.

    Ordering used to be document order, which put AccuLynx's "GIVEAWAY" graphic
    ahead of their product screenshot.
    """
    # Host excluded. Scoring the whole URL let the HOST match _PROMO_IMAGE, so a
    # business at promo-plumbing.com had every one of its photos penalised
    # equally — see the _JUNK_IMAGE note in extract_photos, same root cause.
    bare = urlparse(url).path
    scannable = _sans_host(url)
    score = 0

    # Photographs are overwhelmingly JPEG/WebP. Transparent PNGs are logos,
    # cut-outs and composites — they render badly behind object-fit: cover.
    # Anchored at $, so it reads the bare path: `/photo.jpg?v=2` must still
    # score as a JPEG.
    if re.search(r"\.(jpe?g|webp)$", bare, re.I):
        score += 3

    # Unanchored, so it reads path+query — campaign artwork served as
    # `/cdn?file=giveaway-banner.jpg` has to keep its penalty.
    if _PROMO_IMAGE.search(scannable):
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

# Each upgrade costs a blocking HEAD, and the caller's budget has no slack:
# generation alone is ~82s of the 135s ceiling, on top of a 25s Firecrawl scrape
# and a 15s Pages verify. Six upgrades at 4s each could add 24s and push the
# whole build past a deadline deploy.py's own comment says cannot be raised.
# Worse in v2: these seconds come out of INTEL_BUDGET_SECONDS, which is measured
# around this call, so a slow upgrade silently eats the press search instead.
# Only the hero and its fallback materially benefit from a full-size swap.
_UPGRADE_TOP_N = 2
_UPGRADE_HEAD_TIMEOUT = 2


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
        resp = requests.head(candidate, headers=HEADERS,
                             timeout=_UPGRADE_HEAD_TIMEOUT, allow_redirects=True)
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


def _text_from_html(html: str) -> str:
    """HTML -> the plain text the extraction prompt is built from.

    Shared by the Playwright tier and the direct-fetch tier so the two cannot
    drift. That matters more than it looks: MIN_SITE_TEXT_CHARS decides whether
    a build happens at all and EXTRACT_MAX_CHARS decides what the model sees,
    and both are measured on whatever this returns. Two strippers meant one
    tier's 200 chars was not the other's.

    Stripping the rendered DOM, rather than reading page.inner_text(), is
    deliberate — inner_text returns only what is VISIBLE, which measured 6,156
    chars on acculynx.com against 17,947 here, and dropped a field off the
    extraction. Rendering is supposed to add content, never subtract it.
    """
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _fetch_html_only(url: str) -> tuple:
    """(html, final_url) for photo extraction. Never raises.

    Firecrawl sometimes returns good markdown and an EMPTY `html` field, and
    extract_photos reads html — so the build shipped with zero photos, which
    looks identical to a site that genuinely has none. One cheap GET backfills
    it; the text is already good, so any failure here is non-fatal.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.text, resp.url
    except Exception as e:
        print(f"  [intel] html backfill failed: {e}")
    return "", url


# Scroll to the bottom in viewport-sized steps so IntersectionObserver-based
# lazy loaders actually fire, then return to the top. Bounded twice — by total
# distance and by step count — because an infinite-scroll page grows
# scrollHeight as fast as you consume it and would otherwise never resolve.
_SCROLL_JS = """
() => new Promise(resolve => {
  const step = Math.max(400, window.innerHeight * 0.9);
  let travelled = 0, ticks = 0, timer = null, done = false;

  const finish = () => {
    if (done) return;
    done = true;
    if (timer !== null) clearInterval(timer);
    try { window.scrollTo(0, 0); } catch (e) {}
    resolve(travelled);
  };

  // This promise MUST settle. page.evaluate has NO timeout of its own and does
  // not honour set_default_timeout — measured, an unresolved promise costs
  // ~30s of driver default, which blows both the render budget and the build
  // deadline behind it. Every other bound in here is advisory; this one is not.
  setTimeout(finish, 4000);

  timer = setInterval(() => {
    try {
      window.scrollBy(0, step);
      travelled += step;
      ticks += 1;
      // Guarded, and the counters are checked FIRST. Reading
      // document.body.scrollHeight at the head of an || chain meant a document
      // with no body threw on every tick, so clearInterval and resolve were
      // never reached and the bounded checks behind it never got a vote.
      const height = (document.body && document.body.scrollHeight) || 0;
      if (ticks > 40 || travelled > 30000 || travelled >= height) finish();
    } catch (e) {
      finish();
    }
  }, 60);
})
"""

# Scrolling fires the loaders that are wired to the viewport; this catches the
# ones that are not. Plenty of themes park the real URL in data-src and swap it
# in on an event we never trigger (a carousel tick, a click). Promoting the
# attribute is what extract_photos reads, so the photo is recovered either way.
_PROMOTE_LAZY_JS = """
() => {
  const REAL = /^(https?:|\\/)/;
  let promoted = 0;
  for (const img of document.querySelectorAll('img')) {
    const src = img.getAttribute('src') || '';
    const lazy = img.getAttribute('data-src')
              || img.getAttribute('data-lazy-src')
              || img.getAttribute('data-original')
              || img.getAttribute('data-bg') || '';
    // Only overwrite a placeholder: a 1x1 gif, an inline data: URI, or nothing.
    if (lazy && REAL.test(lazy) && (!src || src.startsWith('data:'))) {
      img.setAttribute('src', lazy);
      promoted += 1;
    }
    const lazySet = img.getAttribute('data-srcset')
                 || img.getAttribute('data-lazy-srcset') || '';
    if (lazySet && !img.getAttribute('srcset')) {
      img.setAttribute('srcset', lazySet);
      promoted += 1;
    }
  }
  return promoted;
}
"""


def _render_with_playwright(url: str, budget_seconds: float = None) -> tuple:
    """(text, html, final_url) from a real rendered page. Never raises.

    Returns ("", "", url) on every failure — a missing browser binary, a
    timeout, a non-200, a crash — so the caller can fall through to the plain
    fetch. A prospect's site being slow must never fail the whole build.

    Sync API on purpose: api.py reaches this through
    `loop.run_in_executor(None, scrape_site, domain)`, so it runs on a worker
    thread with no event loop of its own, which is what sync_playwright needs.
    Calling scrape_site directly from an async handler would break here — the
    sync API refuses to start inside a running asyncio loop.
    """
    if not PLAYWRIGHT_ENABLED:
        return "", "", url

    budget_ms = PLAYWRIGHT_TIMEOUT_MS
    if budget_seconds is not None:
        budget_ms = min(budget_ms, int(budget_seconds * 1000))
    if budget_ms < _PLAYWRIGHT_MIN_SECONDS * 1000:
        print(f"  [intel] Skipping Playwright — only {budget_ms}ms of budget left")
        return "", "", url

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # Deploy-shaped failure, not a site failure: the package or its browser
        # never made it into the image. Say so loudly — silently serving
        # unrendered HTML is exactly the regression this function exists to end.
        print("  [intel] Playwright not installed — falling back to direct fetch")
        return "", "", url

    # The clock starts HERE, not after the queue. Time spent waiting for a
    # browser slot is time the build has spent, so the deadline has to cover it
    # — computing it after the acquire let a 14s queue and a 20s render add up
    # to 34s inside a 20s ceiling.
    deadline = time.monotonic() + budget_ms / 1000.0

    def remaining_ms(floor=500):
        return max(floor, int((deadline - time.monotonic()) * 1000))

    # Queue for a slot only while enough budget would survive the wait to make
    # rendering worth starting. Timing out is a normal outcome under load.
    slot_wait = (deadline - time.monotonic()) - _PLAYWRIGHT_MIN_SECONDS
    if slot_wait <= 0 or not _PLAYWRIGHT_SLOTS.acquire(timeout=slot_wait):
        print(f"  [intel] {PLAYWRIGHT_MAX_CONCURRENT} renders already running — "
              f"falling back to direct fetch")
        return "", "", url

    browser = pw = None
    try:
        pw = sync_playwright().start()
        launch_kwargs = {
            "headless": True,
            # --no-sandbox: containers run as root without user namespaces.
            # --disable-dev-shm-usage: /dev/shm is 64MB in most containers and
            # Chromium crashes mid-render when it fills, which reads as a random
            # scrape failure. Both are required on Railway, harmless locally.
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                     "--disable-extensions", "--mute-audio"],
        }
        # Escape hatch for the build image. `playwright install chromium`
        # downloads a browser that needs glibc shared libraries a Nix-based
        # image does not necessarily have, and its `--with-deps` flag shells out
        # to apt, which nixpacks has no apt to run. Pointing at a chromium the
        # image already provides sidesteps both.
        if PLAYWRIGHT_CHROMIUM_PATH:
            launch_kwargs["executable_path"] = PLAYWRIGHT_CHROMIUM_PATH
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(remaining_ms())

        # domcontentloaded, not networkidle: networkidle never arrives on a page
        # with analytics polling or a live chat widget, and waiting for it would
        # spend the entire budget on sites that render fine. The settle below
        # covers the gap.
        response = page.goto(url, wait_until="domcontentloaded",
                             timeout=remaining_ms())

        # Same rule the fetch path learned the hard way (POD01-15 P1): a browser
        # renders a 401 login wall or a 404 just as happily as a homepage, and
        # the model will write a business out of it. affiliatesolar.com (401,
        # 'Private Site') would sail straight through without this.
        if response is None:
            print("  [intel] Playwright got no response — falling back")
            return "", "", url
        if response.status != 200:
            print(f"  [intel] Playwright: HTTP {response.status} — falling back")
            return "", "", url

        # Best-effort quiet period. Timing out here is normal and not an error:
        # whatever rendered by now is still worth reading.
        try:
            page.wait_for_load_state("networkidle",
                                     timeout=min(4000, remaining_ms()))
        except Exception:
            pass

        try:
            page.evaluate(_SCROLL_JS)
            page.wait_for_timeout(min(_PLAYWRIGHT_SETTLE_MS, remaining_ms()))
            promoted = page.evaluate(_PROMOTE_LAZY_JS)
            if promoted:
                print(f"  [intel] Playwright: promoted {promoted} lazy image refs")
        except Exception as e:
            # A page that blocks scripting still gave us a rendered DOM.
            print(f"  [intel] Playwright: lazy-image pass skipped ({e})")

        # Re-arm before the read. set_default_timeout was last set BEFORE the
        # navigation, so page.content() would otherwise still be working to the
        # full original budget no matter how much of it goto and the scroll
        # already spent.
        page.set_default_timeout(remaining_ms())
        html = page.content()
        text = _text_from_html(html)
        final_url = page.url or url
        print(f"  [intel] Playwright render OK — {len(text)} chars")
        return text, html, final_url

    except Exception as e:
        print(f"  [intel] Playwright failed: {type(e).__name__}: {e} — falling back")
        return "", "", url
    finally:
        # A leaked Chromium survives the request and eats the build container,
        # so every one of these runs even when the body raised. Each is guarded
        # separately: close() on a browser that already crashed raises again,
        # and that must not stop pw.stop() from running.
        for closer in (getattr(browser, "close", None), getattr(pw, "stop", None)):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                pass
        # Last, and outside the closers' own error handling: a slot leaked here
        # is permanent — the semaphore never refills and every later build in
        # this process silently drops to the fetch tier.
        _PLAYWRIGHT_SLOTS.release()


def fetch_site_content(domain: str) -> dict:
    """Scrape the prospect's site.

    Returns {"text": str, "html": str, "final_url": str}.

    `final_url` is the URL the content actually came from, which is NOT always
    the one we asked for — a prospect on olddomain.com may redirect to
    newdomain.com. extract_photos resolves relative `src` values against it, so
    dropping it (as this used to) pointed every relative image path at the
    pre-redirect host, where it 404s.

    Three tiers, each a fallback for the one above, all inside one
    SCRAPE_BUDGET_SECONDS deadline:

      1. Firecrawl  — renders JS, clean markdown, untruncated (V1 cut at 4,000
                      chars, so only half a homepage reached the model).
      2. Playwright — a real browser we drive ourselves. Covers the two things
                      Firecrawl does not: it runs when Firecrawl is down or out
                      of credits, and it SCROLLS, which is the only way to make
                      lazy-loaded photos appear. Measured on the six benchmark
                      sites, Firecrawl read the text fine but still returned
                      zero photos on three of them.
      3. Direct fetch — raw unrendered HTML. Last resort, kept because a
                      prospect on a plain server-rendered site does not need a
                      browser and this cannot fail the way a browser can.
    """
    url = f"https://{domain}" if not domain.startswith("http") else domain
    started = time.monotonic()

    def remaining():
        return SCRAPE_BUDGET_SECONDS - (time.monotonic() - started)

    if FIRECRAWL_KEY:
        try:
            resp = requests.post(
                FIRECRAWL_SCRAPE,
                headers={"Authorization": f"Bearer {FIRECRAWL_KEY}",
                         "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown", "html"], "onlyMainContent": False},
                # Sized to the caller's 135s budget, not to Firecrawl's patience.
                # Capped again by what is left of the scrape budget, so a slow
                # Firecrawl cannot consume the tiers below it.
                timeout=max(5, min(25, remaining())),
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}) or {}
                text = (data.get("markdown") or "").strip()
                html = data.get("html") or ""
                # Length, not truthiness: Firecrawl happily renders a login wall
                # or a parked page and returns it as markdown. A stub here used
                # to short-circuit the direct-fetch fallback and be treated as
                # the real site.
                if len(text) >= MIN_SITE_TEXT_CHARS:
                    final_url = (data.get("metadata") or {}).get("sourceURL") or url
                    # Firecrawl's text is good; its HTML may still be photo-less.
                    # Two different ways that happens, one remedy:
                    #   - it returns markdown and an EMPTY html field
                    #   - it returns html whose <img> tags are all still
                    #     placeholders, because it never scrolled the page
                    # Either way the build ships on gradients, which is
                    # indistinguishable from a business that has no photos.
                    if not _photo_candidates(html, final_url):
                        why = "no html" if not html else "no photos in html"
                        print(f"  [intel] Firecrawl returned {why} — "
                              f"re-rendering for photo extraction")
                        _, p_html, p_url = _render_with_playwright(url, remaining())
                        if _photo_candidates(p_html, p_url or final_url):
                            html, final_url = p_html, (p_url or final_url)
                        elif not html:
                            # Keep the old cheap backfill for the empty-html
                            # case: better a photo-less html than none at all.
                            html, backfilled = _fetch_html_only(url)
                            final_url = backfilled or final_url
                    print(f"  [intel] Firecrawl scrape OK — {len(text)} chars")
                    return {"text": text, "html": html, "final_url": final_url}
                print(f"  [intel] Firecrawl returned {len(text)} chars "
                      f"(under {MIN_SITE_TEXT_CHARS}) — falling back")
            else:
                print(f"  [intel] Firecrawl scrape failed: {resp.status_code} — falling back")
        except Exception as e:
            print(f"  [intel] Firecrawl scrape error: {e} — falling back")
    else:
        print("  [intel] FIRECRAWL_API_KEY not set — trying Playwright")

    # Tier 2: render it ourselves. This is what a Next.js or SPA prospect needs —
    # the direct fetch below sees their empty shell and nothing else.
    p_text, p_html, p_url = _render_with_playwright(url, remaining())
    if len(p_text) >= MIN_SITE_TEXT_CHARS:
        return {"text": p_text, "html": p_html, "final_url": p_url or url}

    # Tier 3: direct fetch, tags stripped. No 4,000-char cap here either.
    try:
        resp = requests.get(url, headers=HEADERS, timeout=max(5, min(15, remaining())))
        # requests does not raise on 4xx/5xx and an error page still carries a
        # body, so without this a 401 login wall or a 404 became "the prospect's
        # website" — and the log still said "OK". `_full_size` below has always
        # checked this; this path never did.
        if resp.status_code != 200:
            print(f"  [intel] Direct fetch failed: HTTP {resp.status_code}")
            return {"text": "", "html": "", "final_url": url}
        html = resp.text
        text = _text_from_html(html)
        print(f"  [intel] Direct fetch OK — {len(text)} chars")
        # resp.url, not url: requests followed the redirects, and this is where
        # the html actually came from.
        return {"text": text, "html": html, "final_url": resp.url or url}
    except Exception as e:
        print(f"  [intel] Fetch failed: {e}")
        return {"text": "", "html": "", "final_url": url}


def _photo_candidates(html: str, base_url: str, limit: int = 6) -> list:
    """The filtering and ranking half of extract_photos — no network.

    Split out so a caller can ask "does this html contain any photos at all?"
    for free. extract_photos pays a blocking HEAD per top candidate
    (_UPGRADE_TOP_N x _UPGRADE_HEAD_TIMEOUT = up to 4s), and fetch_site_content
    now asks that question mid-scrape to decide whether Firecrawl's html is
    worth re-rendering. Asking it through extract_photos would spend those
    seconds twice, inside a budget test_bounds.py already calls tight.
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
        # Before any filtering: an optimiser URL hides both the extension and
        # the filename, so every check below would read the proxy path instead
        # of the image. Publishing the unwrapped origin URL is also the safer
        # end state — the rebuilt site is hosted elsewhere and must not depend
        # on the prospect's own image endpoint still answering for it.
        absolute = _unwrap_image_proxy(absolute)
        if not absolute.startswith(("http://", "https://")):
            continue
        # Extension check stays on the FULL url: _PHOTO_EXT allows the
        # extension to sit in the query (`(\?|$)`), and some CDNs serve
        # `/img?file=photo.jpg`. Narrowing this to the path would drop those —
        # re-creating, on a different set of sites, the silent zero-photo
        # failure being fixed two lines below.
        if not _PHOTO_EXT.search(absolute):
            continue          # skips .svg, .gif and extensionless endpoints
        # The JUNK check, by contrast, must not see the HOST. Against the full
        # url it also matched the host, and _JUNK_IMAGE contains substrings that
        # occur in ordinary business names — so a prospect at
        # blankslatecoffee.com, badgerroofing.com, arrowplumbing.com or
        # iconicdental.com matched on their own domain, lost every candidate,
        # and fell back to gradients with nothing logged. Same silent-fallback
        # shape as the Yelp 403 this code replaced.
        #
        # Host only — NOT the query. `_sans_host`, not `urlparse(...).path`: the
        # same CDNs that put the extension in the query put the filename there
        # too, so a path-only check waves `/cdn?file=logo.png` through and the
        # prospect's logo becomes the hero of their own rebuilt page.
        if _JUNK_IMAGE.search(_sans_host(absolute)):
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
    return [url for url, _ in kept[:limit]]


def extract_photos(html: str, base_url: str, limit: int = 6) -> list:
    """Pull real photos of the business off their own page.

    Prefers the og:image (almost always the hero shot), then <img> sources,
    skipping the logos, icons and tracking pixels that make up most <img> tags
    on a small business site.
    """
    # Only the top candidates pay for a full-size lookup — see _UPGRADE_TOP_N.
    return [_full_size(url) if i < _UPGRADE_TOP_N else url
            for i, url in enumerate(_photo_candidates(html, base_url, limit))]


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


# Bound the extraction prompt. Dropping the 4,000-char scrape cap was right —
# it truncated half a homepage before the model ever saw it — but nothing
# replaced it, so the full page went into this Haiku call with no ceiling.
# Firecrawl runs with onlyMainContent: False, and the direct-fetch fallback
# strips tags off raw HTML so inline JSON survives as "text"; both can be very
# large. 60k chars is roughly 15k tokens: comfortably a whole homepage, and
# small enough that no page can fail a build on prompt size.
#
# Note the asymmetry this restores: scrape_site already caps the GENERATOR's
# slice at raw_text[:6000]. Only the extraction prompt was unbounded.
EXTRACT_MAX_CHARS = int(os.environ.get("EXTRACT_MAX_CHARS", "60000"))


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
- neighborhood: The specific neighborhood or district the business is in, parsed from the location field (string, e.g. "North Park", "Wicker Park", "Southie", empty if unknown)

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


def scrape_site(domain: str, page_url: str = "") -> dict:
    """Full intel gather for a prospect domain.

    `page_url` is the lead's OWN page when the caller has one — the full stored
    website URL, path included. `domain` stays the bare host regardless, because
    the slug, the queue join and the Claude extraction prompt all key on it.

    WHY (POD01-34): this function used to flatten the path and fetch the root, so
    a business that lives inside a larger site got the PARENT scraped. A
    restaurant at `ateliers-atbs.fr/restauration/` was built as the charity that
    owns the building — conditioning, laundry and floral services — with the
    restaurant's own 4.9 stars and phone number layered on top. Nothing looked
    broken, which is why it shipped. 25% of leads store a URL with a path.

    Falls back to `https://{domain}` when no page_url is given, so every caller
    that doesn't send one (lm-tool, run_engine.py, smoke tests) is unaffected.
    """

    started = time.monotonic()
    domain = domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    # The scrape target and the identity are now two different things. Everything
    # downstream still identifies the business by `domain`; only the fetch moves.
    url = (page_url or "").strip() or f"https://{domain}"
    print(f"  [intel] Fetching {url}...")

    # fetch_site_content already passes a full URL through untouched
    # (`if not domain.startswith("http")`), so it needs no change.
    scraped = fetch_site_content(url)
    raw_text, raw_html = scraped["text"], scraped["html"]

    # Never build from an unread site. Without this the model invents the whole
    # business from the domain string and we publish a fiction under a real
    # company's name — see familydentalcare.com, 2026-08-04.
    #
    # Length, not emptiness. The original `if not raw_text` only caught a blank
    # response, so affiliatesolar.com (HTTP 401 -> 'Private Site &nbsp;', 19
    # chars) and solaricon.tech ('Coming Soon!', 12) sailed through and reached
    # the model as though they were real homepages — the same fabricated-site
    # outcome this guard was written to stop, just via a different door.
    if len(raw_text) < MIN_SITE_TEXT_CHARS:
        raise ValueError(
            f"Could not read {url} — got {len(raw_text)} chars of text, need at "
            f"least {MIN_SITE_TEXT_CHARS}. Refusing to build a site for a "
            f"business we never read."
        )

    print(f"  [intel] Extracting structured intel with Claude...")
    if len(raw_text) > EXTRACT_MAX_CHARS:
        print(f"  [intel] Page is {len(raw_text)} chars — capping extraction "
              f"input at {EXTRACT_MAX_CHARS}")
    # Deliberately NOT wrapped in a try/except that returns {}. With extracted
    # empty, business_name falls back to the domain string and every other field
    # to its default — which is the fabricated-business path the empty-scrape
    # guard above exists to block. An extraction failure must keep propagating
    # so the build fails cleanly and refunds. The cap is what makes it rare.
    extracted = extract_intel_with_claude(domain, raw_text[:EXTRACT_MAX_CHARS])

    business_name = extracted.get("business_name") or domain.split(".")[0].replace("-", " ").title()
    location = extracted.get("location", "")

    # Photos come from the prospect's own page now. Yelp 403s datacenter IPs,
    # so the old path returned nothing on every single build.
    # Resolve relative image paths against where the html ACTUALLY came from.
    # Using `url` here sent every relative src to the pre-redirect host, so a
    # prospect who moved domains lost all their photos to 404s.
    photos = extract_photos(raw_html, scraped.get("final_url") or url)
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
        # Rating and review count are real data the app already holds (Google
        # Maps via Apify). api.py merges them in from engine_queue when present.
        # We no longer scrape them — the old Yelp regex matched any JSON field
        # named "text" and handed arbitrary strings to the model as verbatim
        # customer testimonials, which is worse than inventing one.
        #
        # There is no "reviews" key: review TEXT has no trusted source, so the
        # generator has no branch that could consume one. Adding it back here
        # would silently re-arm quote generation.
        "rating": None,
        "review_count": None,
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
    # Was `!= "San Diego, CA"`, which stood in for "location was not extracted"
    # back when that string was the default. v2 already changed the default to
    # empty, which left this comparison dead — it could only ever fire against a
    # business genuinely in San Diego, silently scoring them 3 points lower than
    # an identical one in Boston. The truthiness check is now the whole test.
    if intel.get("location"): contact_score += 3
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
