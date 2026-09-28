"""Two of the business's own social posts for the Sponsored Story (POD01-239).

The live ThereSanDiego article embeds two real Instagram posts from the
business it features: the photo, the likes, "View more on Instagram", and a
click that opens the post itself. Our story carried one profile card with a
"View profile" button, because all we ever held was the profile URL.

The flow, agreed with Fazal on 28 Sep:
  1. The business has socials: fetch its latest posts, have Claude look at each
     caption AND photo, keep only posts about the business, show the best two.
  2. No socials, or none with a usable post: show photos off their own website
     that the article did not already use.
  3. Neither: no post section at all. A "This account is private" box on a
     sales preview reads as a broken page; an absent one is never noticed.

Every platform goes through Apify (APIFY_TOKEN on Railway). Without the token
this module returns no posts and step 2 takes over, so the engine deploys and
builds exactly as before the day the key is missing.

LinkedIn is left out on purpose. Its company post scrapers are third-party
actors and its embed wants an activity URN the page URL does not carry.

Runs in a background thread BESIDE the article generation, never after it.
build-smart-site aborts at 160s and real builds already run 106-150s, so a
serial Apify run (20-40s per platform) would push builds over the edge.
"""

import base64
import concurrent.futures
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import anthropic
import requests

import cost
from claude_text import first_text
from intel import _PHOTO_INLINE_MAX, _fetch_one_photo

APIFY_RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

# Instagram first: it is the platform the live article embeds. The rest follow
# the order the old profile card used.
PLATFORM_ORDER = ["instagram_url", "facebook_url", "tiktok_url", "youtube_url"]

# Enough to find two good posts in an account that also posts personal ones,
# few enough that the review stays one cheap call.
CANDIDATES_PER_PLATFORM = int(os.environ.get("SOCIAL_POSTS_CANDIDATES", "12"))
WANT = 2
MAX_POST_AGE_DAYS = int(os.environ.get("SOCIAL_POSTS_MAX_AGE_DAYS", "365"))

# The whole job, from the moment generation starts. Generation itself takes
# ~82-95s, so a job inside this budget costs the build no extra wall clock.
BUDGET_SECONDS = float(os.environ.get("SOCIAL_POSTS_BUDGET_SECONDS", "90"))
# Do not start another platform with less than this left: one Apify run plus
# the review would not finish in time, and a platform we cannot use is paid for.
_MIN_SECONDS_FOR_A_PLATFORM = 40
_APIFY_MAX_RUN_SECONDS = 60
# How long past its deadline the build waits for a review already under way.
_COLLECT_GRACE_SECONDS = 5

REVIEW_MODEL = "claude-haiku-4-5"
_REVIEW_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_CAPTION_CHARS = 500

# One thread per build is plenty; this only bounds a burst of parallel builds.
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="social-posts")


def _token() -> str:
    # Read per call, not at import, so tests and a Railway variable change both
    # take effect without a restart of the import.
    return os.environ.get("APIFY_TOKEN", "")


# ── Apify ────────────────────────────────────────────────────────────────────

def _run_actor(actor: str, payload: dict, seconds: float) -> list:
    """Run one actor and return its dataset items, or [] on any failure.

    A failure here is never a failed build: the story falls back to website
    photos, which is what it would show for a business with no socials.
    """
    token = _token()
    if not token:
        return []
    run_seconds = int(max(10, min(_APIFY_MAX_RUN_SECONDS, seconds)))
    try:
        resp = requests.post(
            APIFY_RUN_SYNC.format(actor=actor),
            params={"timeout": run_seconds},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=run_seconds + 15,
        )
        if resp.status_code not in (200, 201):
            print(f"  [social] {actor} answered {resp.status_code}")
            return []
        items = resp.json()
        return items if isinstance(items, list) else []
    except Exception as e:
        print(f"  [social] {actor} failed: {e}")
        return []


def _parse_time(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _count(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _host_is(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _instagram(profile_url: str, seconds: float) -> list:
    items = _run_actor("apify~instagram-post-scraper", {
        "username": [profile_url],
        "resultsLimit": CANDIDATES_PER_PLATFORM,
        "onlyPostsNewerThan": _since(MAX_POST_AGE_DAYS),
    }, seconds)
    posts = []
    for it in items:
        url = it.get("url") or ""
        if it.get("shortCode") and "/reel/" not in url:
            url = f"https://www.instagram.com/p/{it['shortCode']}/"
        if not _host_is(url, "instagram.com"):
            continue
        posts.append({
            "platform": "instagram",
            "url": url,
            "caption": it.get("caption") or "",
            "image": it.get("displayUrl") or "",
            "likes": _count(it.get("likesCount")),
            "posted_at": _parse_time(it.get("timestamp")),
        })
    return posts


def _facebook(page_url: str, seconds: float) -> list:
    items = _run_actor("apify~facebook-posts-scraper", {
        "startUrls": [{"url": page_url}],
        "resultsLimit": CANDIDATES_PER_PLATFORM,
        "onlyPostsNewerThan": _since(MAX_POST_AGE_DAYS),
    }, seconds)
    posts = []
    for it in items:
        url = it.get("url") or ""
        if not _host_is(url, "facebook.com"):
            continue
        media = (it.get("media") or [{}])[0] or {}
        image = ((media.get("photo_image") or {}).get("uri")) or media.get("thumbnail") or ""
        posts.append({
            "platform": "facebook",
            "url": url,
            "caption": it.get("text") or "",
            "image": image,
            "likes": _count(it.get("likes")),
            "posted_at": _parse_time(it.get("time") or it.get("timestamp")),
        })
    return posts


def _tiktok_handle(profile_url: str) -> str:
    match = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", profile_url)
    return match.group(1) if match else ""


def _tiktok(profile_url: str, seconds: float) -> list:
    handle = _tiktok_handle(profile_url)
    if not handle:
        return []
    items = _run_actor("clockworks~tiktok-scraper", {
        "profiles": [handle],
        "resultsPerPage": CANDIDATES_PER_PLATFORM,
        "profileSorting": "latest",
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
    }, seconds)
    posts = []
    for it in items:
        url = it.get("webVideoUrl") or ""
        video_id = re.search(r"/video/(\d+)", url)
        if not video_id or not _host_is(url, "tiktok.com"):
            continue
        posts.append({
            "platform": "tiktok",
            "url": url,
            "embed_id": video_id.group(1),
            "caption": it.get("text") or "",
            "image": (it.get("videoMeta") or {}).get("coverUrl") or "",
            "likes": _count(it.get("diggCount")),
            "posted_at": _parse_time(it.get("createTimeISO")),
        })
    return posts


def _youtube_id(url: str) -> str:
    parsed = urlparse(url)
    if _host_is(url, "youtu.be"):
        candidate = parsed.path.strip("/")
    else:
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate or "") else ""


def _youtube(channel_url: str, seconds: float) -> list:
    items = _run_actor("streamers~youtube-scraper", {
        "startUrls": [{"url": channel_url}],
        "maxResults": CANDIDATES_PER_PLATFORM,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
        "sortVideosBy": "NEWEST",
    }, seconds)
    posts = []
    for it in items:
        url = it.get("url") or ""
        video_id = _youtube_id(url)
        if not video_id:
            continue
        title = (it.get("title") or "").strip()
        text = (it.get("text") or "").strip()
        posts.append({
            "platform": "youtube",
            "url": url,
            "embed_id": video_id,
            "caption": f"{title}. {text}" if title and text else (title or text),
            "image": it.get("thumbnailUrl") or "",
            "likes": _count(it.get("likes")),
            "posted_at": _parse_time(it.get("date")),
        })
    return posts


_FETCHERS = {
    "instagram_url": _instagram,
    "facebook_url": _facebook,
    "tiktok_url": _tiktok,
    "youtube_url": _youtube,
}


def _usable(posts: list, now: Optional[datetime] = None) -> list:
    """Drop what no review could rescue: repeats, old posts, empty posts."""
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=MAX_POST_AGE_DAYS)
    seen, out = set(), []
    for post in posts:
        url = post.get("url")
        if not url or url in seen:
            continue
        # An unknown date is kept: the actors ask for recent posts already, and
        # dropping a good post over a missing field is the worse mistake.
        if post.get("posted_at") and post["posted_at"] < oldest:
            continue
        if not post.get("caption", "").strip() and not post.get("image"):
            continue
        seen.add(url)
        out.append(post)
    return out


# ── the review ───────────────────────────────────────────────────────────────

def _review_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY") or "")


def _images_for_review(posts: list) -> list:
    """Each post's photo as (mime, bytes), or None where it cannot be read.

    Downloaded here and sent as bytes rather than handed to the API as a URL:
    social CDNs sign and expire their links, and one URL the API cannot fetch
    fails the whole request. A post whose photo will not download is still
    reviewed, on its caption alone.
    """
    urls = [p.get("image") or "" for p in posts]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        got = list(pool.map(lambda u: _fetch_one_photo(u) if u else None, urls))
    return [(g[1], g[2]) if g and g[1] in _REVIEW_IMAGE_TYPES else None for g in got]


def _review_prompt(intel: dict, platform: str, count: int) -> str:
    services = ", ".join(intel.get("services") or []) or "not listed"
    return f"""You are choosing which of a business's own {platform} posts to show inside a short
article about that business on ThereSanDiego.com, a San Diego local magazine.

The business: {intel.get('business_name', '')}
Type: {intel.get('business_type') or 'not given'}
What they do: {intel.get('description') or 'not given'}
Services: {services}

Below are {count} of their recent posts, numbered, each with its caption and (where available)
its photo. Look at BOTH the caption and the photo.

KEEP a post only if it is clearly about this business: their work, products, food, space, team
at work, listings or projects, events, offers, or happy customers at the business.

SKIP a post if it is: personal or family life, a holiday greeting, a meme, a repost of someone
else's content, off-topic, political, rude, blurry or low quality, or text on a plain background
with no real photo.

Put the kept posts in order, best first. A clear, good-looking photo that shows the business
beats a busy or dark one; use the like count only to break a tie. Never keep two posts that look
nearly the same.

Reply with JSON only, no other text: {{"keep": [post numbers, best first]}}
If no post qualifies, reply {{"keep": []}}."""


def review_posts(posts: list, intel: dict, meter=None) -> list:
    """The posts worth showing, best first. [] when none are, or on any error.

    An error keeps nothing rather than everything: an unreviewed post could be
    a family photo, and the website photos are a safe second choice.
    """
    if not posts:
        return []
    platform = posts[0].get("platform", "social")
    content = [{"type": "text", "text": _review_prompt(intel, platform, len(posts))}]
    for number, (post, image) in enumerate(zip(posts, _images_for_review(posts)), start=1):
        caption = " ".join((post.get("caption") or "").split())[:_CAPTION_CHARS] or "(no caption)"
        content.append({"type": "text",
                        "text": f"Post {number} ({post.get('likes', 0)} likes). Caption: {caption}"})
        if image:
            mime, blob = image
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": mime,
                "data": base64.b64encode(blob).decode("ascii")}})
    try:
        response = _review_client().messages.create(
            model=REVIEW_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": content}],
        )
        cost.record(meter, f"social_review:{platform}", REVIEW_MODEL, response)
        match = re.search(r"\{.*\}", first_text(response), re.DOTALL)
        keep = json.loads(match.group(0)).get("keep", []) if match else []
    except Exception as e:
        print(f"  [social] review of {platform} posts failed: {e}")
        return []

    picked, seen = [], set()
    for number in keep if isinstance(keep, list) else []:
        if isinstance(number, int) and 1 <= number <= len(posts) and number not in seen:
            seen.add(number)
            picked.append(posts[number - 1])
    return picked


# ── the job ──────────────────────────────────────────────────────────────────

def pick_posts(intel: dict, meter=None, deadline: Optional[float] = None,
               chosen: Optional[list] = None) -> list:
    """Up to two reviewed posts, going through the platforms in order and
    stopping as soon as two are found, so a platform we do not need is never
    paid for.

    `chosen` is filled in place, so a caller that stops waiting still gets the
    posts already picked from an earlier platform.
    """
    chosen = [] if chosen is None else chosen
    if not _token():
        print("  [social] APIFY_TOKEN not set, skipping social posts")
        return chosen
    socials = {k: v for k, v in (intel.get("socials") or {}).items() if v}
    deadline = deadline or (time.monotonic() + BUDGET_SECONDS)
    for key in PLATFORM_ORDER:
        if len(chosen) >= WANT:
            break
        if key not in socials:
            continue
        left = deadline - time.monotonic()
        if left < _MIN_SECONDS_FOR_A_PLATFORM:
            print(f"  [social] {left:.0f}s left, not starting {key}")
            break
        candidates = _usable(_FETCHERS[key](socials[key], left - 20))
        print(f"  [social] {key}: {len(candidates)} usable post(s)")
        if candidates:
            chosen.extend(review_posts(candidates, intel, meter)[:WANT - len(chosen)])
    print(f"  [social] picked {len(chosen)} post(s)")
    return chosen


def _extra_photos(intel: dict) -> dict:
    """Website photos past the ones offered to the model, as {url: data-uri}.

    The model is only ever offered the first _PHOTO_INLINE_MAX, and those are
    the only ones intel downloads, so anything after them is certain to be
    unused by the article and has nothing to inline it with.
    """
    urls = [u for u in (intel.get("photos") or [])[_PHOTO_INLINE_MAX:] if u][:WANT]
    if not urls:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as pool:
        got = list(pool.map(_fetch_one_photo, urls))
    return {g[0]: "data:%s;base64,%s" % (g[1], base64.b64encode(g[2]).decode("ascii"))
            for g in got if g}


def _job(intel: dict, meter, deadline: float, result: dict) -> None:
    try:
        result["extra_photos"].update(_extra_photos(intel))
    except Exception as e:
        print(f"  [social] extra website photos failed: {e}")
    try:
        pick_posts(intel, meter, deadline, chosen=result["posts"])
    except Exception as e:
        print(f"  [social] picking posts failed: {e}")


def start(intel: dict, meter=None):
    """Start the job beside generation. Hand the return value to collect()."""
    deadline = time.monotonic() + BUDGET_SECONDS
    result = {"posts": [], "extra_photos": {}}
    return deadline, result, _POOL.submit(_job, intel, meter, deadline, result)


def collect(started) -> dict:
    """What the job found, waiting at most until its own deadline.

    Past the deadline the build moves on with whatever is already there: posts
    picked from an earlier platform, and the website photos. The thread
    finishes on its own with nothing waiting for it.
    """
    if not started:
        return {"posts": [], "extra_photos": {}}
    deadline, result, future = started
    try:
        future.result(timeout=max(deadline - time.monotonic(), 0) + _COLLECT_GRACE_SECONDS)
    except concurrent.futures.TimeoutError:
        print("  [social] ran out of time, using what was found so far")
    except Exception as e:
        print(f"  [social] job failed: {e}")
    return {"posts": list(result["posts"]), "extra_photos": dict(result["extra_photos"])}
