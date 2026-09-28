"""The Sponsored Story's post section (POD01-239).

The live ThereSanDiego article embeds two of the business's own Instagram
posts. The flow agreed on 28 Sep: fetch the business's recent posts, keep only
the ones a review of caption AND photo says are about the business, show the
best two; fill any gap with website photos the article did not already use;
with neither, show no section at all.

What these pin: nothing reaches the page that the platform did not return,
nothing unreviewed is shown, a platform is never paid for once two posts are
found, and the post section never lands inside another block of the story.
"""

import concurrent.futures
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import generator
import social_posts
import tsd_theme
from test_generate_offer_lead_magnet_page import _intel, _mock_client
from test_sponsored_story_chrome import FRAGMENT, HERO

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _post(n, platform="instagram", **kw):
    base = {
        "platform": platform,
        "url": f"https://www.instagram.com/p/POST{n}/",
        "caption": f"caption {n}",
        "image": f"https://cdn.example.com/{n}.jpg",
        "likes": n,
        "posted_at": NOW - timedelta(days=3),
    }
    base.update(kw)
    return base


# ── what each platform returns ───────────────────────────────────────────────

def test_instagram_posts_are_read_off_the_actor_with_a_clean_permalink(monkeypatch):
    calls = []

    def fake_run(actor, payload, seconds):
        calls.append((actor, payload))
        return [{"url": "https://www.instagram.com/p/ABC/?img_index=1", "shortCode": "ABC",
                 "caption": "Open house Sunday", "displayUrl": "https://cdn.ig/abc.jpg",
                 "likesCount": 170, "timestamp": "2026-09-01T10:00:00.000Z"},
                {"url": "https://evil.example.com/p/X/", "caption": "not instagram"}]

    monkeypatch.setattr(social_posts, "_run_actor", fake_run)
    posts = social_posts._instagram("https://www.instagram.com/aussiejosh.nbhd", 50)

    assert calls[0][0] == "apify~instagram-post-scraper"
    assert calls[0][1]["username"] == ["https://www.instagram.com/aussiejosh.nbhd"]
    assert len(posts) == 1
    assert posts[0]["url"] == "https://www.instagram.com/p/ABC/"
    assert posts[0]["likes"] == 170
    assert posts[0]["image"] == "https://cdn.ig/abc.jpg"


def test_facebook_takes_the_full_photo_off_the_media_list(monkeypatch):
    monkeypatch.setattr(social_posts, "_run_actor", lambda a, p, s: [{
        "url": "https://www.facebook.com/theirs/posts/pfbid0abc", "text": "New listing",
        "time": "2026-09-20T13:00:05.000Z", "likes": 12,
        "media": [{"thumbnail": "https://fb/thumb.jpg", "photo_image": {"uri": "https://fb/full.jpg"}}],
    }])
    posts = social_posts._facebook("https://www.facebook.com/theirs", 50)

    assert posts[0]["image"] == "https://fb/full.jpg"
    assert posts[0]["caption"] == "New listing"


def test_tiktok_and_youtube_carry_the_id_their_embed_needs(monkeypatch):
    monkeypatch.setattr(social_posts, "_run_actor", lambda a, p, s: [
        {"webVideoUrl": "https://www.tiktok.com/@theirs/video/7300000000000000001", "text": "tour"},
        {"webVideoUrl": "https://www.tiktok.com/@theirs/photo/1", "text": "no video id"},
    ])
    tiktok = social_posts._tiktok("https://www.tiktok.com/@theirs", 50)
    assert [p["embed_id"] for p in tiktok] == ["7300000000000000001"]

    monkeypatch.setattr(social_posts, "_run_actor", lambda a, p, s: [
        {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Home tour", "text": "Del Mar"},
        {"url": "https://www.youtube.com/watch?v=short", "title": "bad id"},
    ])
    youtube = social_posts._youtube("https://www.youtube.com/@theirs", 50)
    assert [p["embed_id"] for p in youtube] == ["dQw4w9WgXcQ"]
    assert youtube[0]["caption"] == "Home tour. Del Mar"


def test_a_tiktok_link_with_no_handle_is_never_fetched(monkeypatch):
    monkeypatch.setattr(social_posts, "_run_actor", lambda *a: 1 / 0)
    assert social_posts._tiktok("https://www.tiktok.com/", 50) == []


def test_no_token_means_no_apify_call(monkeypatch):
    monkeypatch.setattr(social_posts.requests, "post", lambda *a, **k: 1 / 0)
    assert social_posts._run_actor("apify~instagram-post-scraper", {}, 30) == []


def test_an_apify_error_is_no_posts_not_a_failed_build(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setattr(social_posts.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=402, json=lambda: {}))
    assert social_posts._run_actor("apify~instagram-post-scraper", {}, 30) == []

    def boom(*a, **k):
        raise TimeoutError("slow")
    monkeypatch.setattr(social_posts.requests, "post", boom)
    assert social_posts._run_actor("apify~instagram-post-scraper", {}, 30) == []


def test_old_empty_and_repeated_posts_are_dropped_before_the_review():
    posts = [
        _post(1),
        _post(1),                                                  # the same post twice
        _post(2, posted_at=NOW - timedelta(days=400)),              # over a year old
        _post(3, caption="  ", image=""),                           # nothing to judge
        _post(4, posted_at=None),                                   # date unknown: kept
    ]
    kept = social_posts._usable(posts, now=NOW)

    assert [p["url"] for p in kept] == [_post(1)["url"], _post(4)["url"]]


# ── the review ───────────────────────────────────────────────────────────────

def _fake_review(monkeypatch, reply, captured=None):
    def create(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)], usage=None)

    monkeypatch.setattr(social_posts, "_review_client",
                        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_the_review_sees_every_caption_and_photo(monkeypatch):
    captured = []
    _fake_review(monkeypatch, '{"keep": [2]}', captured)
    monkeypatch.setattr(social_posts, "_fetch_one_photo",
                        lambda url: (url, "image/jpeg", b"jpg") if url.endswith("1.jpg") else None)

    social_posts.review_posts([_post(1), _post(2)], _intel())
    content = captured[0]["messages"][0]["content"]

    texts = " ".join(b["text"] for b in content if b["type"] == "text")
    assert "caption 1" in texts and "caption 2" in texts
    assert "Dark Horse Coffee Roasters" in texts
    # Post 1's photo downloaded, post 2's did not: post 2 is judged on its caption.
    images = [b for b in content if b["type"] == "image"]
    assert len(images) == 1
    assert images[0]["source"]["type"] == "base64"


def test_the_review_decides_which_posts_and_in_what_order(monkeypatch):
    _fake_review(monkeypatch, 'Here you go: {"keep": [3, 1, 3, 9, "2"]}')
    monkeypatch.setattr(social_posts, "_fetch_one_photo", lambda url: None)

    picked = social_posts.review_posts([_post(1), _post(2), _post(3)], _intel())

    # Best first as given; the repeat, the out-of-range number and the string are ignored.
    assert [p["caption"] for p in picked] == ["caption 3", "caption 1"]


def test_a_failed_review_shows_nothing_rather_than_everything(monkeypatch):
    """An unreviewed post could be a family photo. Website photos are the safe
    stand-in, so an error keeps nothing."""
    monkeypatch.setattr(social_posts, "_fetch_one_photo", lambda url: None)
    _fake_review(monkeypatch, RuntimeError("overloaded"))
    assert social_posts.review_posts([_post(1)], _intel()) == []

    _fake_review(monkeypatch, "I think they all look nice")
    assert social_posts.review_posts([_post(1)], _intel()) == []


# ── going through the platforms ──────────────────────────────────────────────

def _platforms(monkeypatch, found):
    """Fake every fetcher; `found` maps a socials key to the posts it returns."""
    fetched = []

    def fetcher(key):
        def fetch(url, seconds):
            fetched.append(key)
            return found.get(key, [])
        return fetch

    monkeypatch.setattr(social_posts, "_FETCHERS", {k: fetcher(k) for k in social_posts.PLATFORM_ORDER})
    monkeypatch.setattr(social_posts, "_usable", lambda posts: posts)
    monkeypatch.setattr(social_posts, "review_posts", lambda posts, intel, meter=None: posts)
    return fetched


ALL_SOCIALS = {
    "instagram_url": "https://www.instagram.com/theirs",
    "facebook_url": "https://www.facebook.com/theirs",
    "tiktok_url": "https://www.tiktok.com/@theirs",
}


def test_two_good_instagram_posts_mean_no_other_platform_is_paid_for(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    fetched = _platforms(monkeypatch, {"instagram_url": [_post(1), _post(2), _post(3)]})

    picked = social_posts.pick_posts(_intel(socials=ALL_SOCIALS))

    assert [p["caption"] for p in picked] == ["caption 1", "caption 2"]
    assert fetched == ["instagram_url"]


def test_one_post_short_moves_on_to_the_next_platform(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    fetched = _platforms(monkeypatch, {
        "instagram_url": [_post(1)],
        "facebook_url": [_post(2, platform="facebook"), _post(3, platform="facebook")],
    })

    picked = social_posts.pick_posts(_intel(socials=ALL_SOCIALS))

    assert [p["platform"] for p in picked] == ["instagram", "facebook"]
    assert fetched == ["instagram_url", "facebook_url"]


def test_a_platform_is_not_started_without_time_to_finish_it(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    fetched = _platforms(monkeypatch, {"instagram_url": [_post(1)]})

    picked = social_posts.pick_posts(_intel(socials=ALL_SOCIALS), deadline=time.monotonic() + 5)

    assert picked == [] and fetched == []


def test_without_a_token_no_platform_is_tried(monkeypatch):
    fetched = _platforms(monkeypatch, {"instagram_url": [_post(1)]})

    assert social_posts.pick_posts(_intel(socials=ALL_SOCIALS)) == []
    assert fetched == []


def test_a_job_that_runs_late_still_hands_over_what_it_found(monkeypatch):
    """Past the deadline the build moves on, keeping the posts already picked."""
    monkeypatch.setattr(social_posts, "_COLLECT_GRACE_SECONDS", 0)
    never = concurrent.futures.Future()
    result = {"posts": [_post(1)], "extra_photos": {"https://site/x.jpg": "data:image/jpeg;base64,eA=="}}

    found = social_posts.collect((time.monotonic() - 10, result, never))

    assert found["posts"] == [_post(1)]
    assert "https://site/x.jpg" in found["extra_photos"]
    assert social_posts.collect(None) == {"posts": [], "extra_photos": {}}


# ── the markup ───────────────────────────────────────────────────────────────

def test_each_platform_is_drawn_with_its_own_embed():
    ig = tsd_theme.post_embed(_post(1, url="https://www.instagram.com/p/ABC/?igsh=track"))
    assert 'class="instagram-media"' in ig
    assert 'data-instgrm-permalink="https://www.instagram.com/p/ABC/?utm_source=ig_embed"' in ig
    assert "igsh" not in ig

    fb = tsd_theme.post_embed(_post(2, platform="facebook", url="https://www.facebook.com/x/posts/1"))
    assert 'class="fb-post" data-href="https://www.facebook.com/x/posts/1"' in fb

    tt = tsd_theme.post_embed(_post(3, platform="tiktok", embed_id="73",
                                    url="https://www.tiktok.com/@x/video/73"))
    assert 'class="tiktok-embed"' in tt and 'data-video-id="73"' in tt

    yt = tsd_theme.post_embed(_post(4, platform="youtube", embed_id="dQw4w9WgXcQ",
                                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in yt


def test_a_post_that_is_not_https_or_not_a_known_platform_is_dropped():
    assert tsd_theme.post_embed(_post(1, url="javascript:alert(1)")) == ""
    assert tsd_theme.post_embed(_post(1, platform="linkedin")) == ""
    assert tsd_theme.post_embed(_post(1, platform="tiktok")) == ""  # no video id


def test_each_embed_script_is_loaded_once():
    scripts = tsd_theme.post_scripts([_post(1), _post(2), _post(3, platform="youtube")])

    assert scripts.count("instagram.com/embed.js") == 1
    assert "tiktok" not in scripts and "connect.facebook.net" not in scripts


def test_the_photo_card_opens_their_website():
    card = tsd_theme.photo_card("https://darkhorsecoffeeroasters.com/img/roast.jpg", _intel())

    assert 'src="https://darkhorsecoffeeroasters.com/img/roast.jpg"' in card
    assert card.count('href="https://darkhorsecoffeeroasters.com"') == 3
    assert "Dark Horse Coffee Roasters" in card
    assert "View more on darkhorsecoffeeroasters.com" in card


# ── where the posts go ───────────────────────────────────────────────────────

LONG = ("<h1>H</h1>\n<p>one</p>\n<p>two</p>\n<h2>First</h2>\n<p>three</p>\n<p>four</p>\n"
        "<h2>Second</h2>\n<p>five</p>\n<p>six</p>")


def test_the_first_post_follows_the_second_paragraph_and_the_second_waits_for_a_subheading():
    out = tsd_theme.insert_story_posts(LONG, ["[A]", "[B]"])

    assert out.index("<p>two</p>") < out.index("[A]") < out.index("<h2>First</h2>")
    # Not the subheading straight after the first post: two paragraphs sit between them.
    assert out.index("<p>four</p>") < out.index("[B]") < out.index("<h2>Second</h2>")


def test_a_post_never_lands_inside_the_pull_quote():
    article = ('<h1>H</h1>\n<p>one</p>\n<div class="tsd-pullquote"><p>quote</p></div>\n'
               "<p>two</p>\n<p>three</p>")
    out = tsd_theme.insert_story_posts(article, ["[A]"])

    assert out.index("</div>") < out.index("[A]")
    assert out.index("<p>two</p>") < out.index("[A]") < out.index("<p>three</p>")


def test_a_short_story_gets_its_posts_one_after_the_other_under_the_hero():
    article = '<h1>H</h1>\n<img class="tsd-hero" src="a.jpg" alt="">\n<p>only one</p>'
    out = tsd_theme.insert_story_posts(article, ["[A]", "[B]"])

    assert out.index("tsd-hero") < out.index("[A]") < out.index("[B]") < out.index("<p>only one</p>")


def test_no_posts_leaves_the_article_untouched():
    assert tsd_theme.insert_story_posts(LONG, []) == LONG
    assert tsd_theme.insert_story_posts(LONG, ["", ""]) == LONG


def test_the_embed_scripts_follow_the_article():
    out = tsd_theme.insert_story_posts(LONG, ["[A]"], "<script>s</script>")
    assert out.endswith("<script>s</script>")


# ── the finished story ───────────────────────────────────────────────────────

SPARE = "https://darkhorsecoffeeroasters.com/img/spare.jpg"


def _build(monkeypatch, found, intel=None, assets=None, offer="sponsored_story"):
    started = []
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client([], html=FRAGMENT))
    monkeypatch.setattr(social_posts, "start", lambda intel, meter=None: started.append(1) or "job")
    monkeypatch.setattr(social_posts, "collect", lambda job: found)
    page = generator.generate_offer_lead_magnet_page(
        offer, intel or _intel(photos=[HERO, SPARE]),
        prospect_id="darkhorsecoffeeroasters-com---sponsored-story",
        public_base="https://www.gotheresandiego.com", photo_assets=assets,
    )
    return page, started


def _main(html):
    return html[html.index('<div class="tsd-main">'):html.index('<aside class="tsd-side">')]


def test_two_posts_are_embedded_in_the_story(monkeypatch):
    html, started = _build(monkeypatch, {"posts": [_post(1), _post(2)], "extra_photos": {}})
    main = _main(html)

    assert started == [1]
    assert main.count('class="instagram-media"') == 2
    assert main.count("instagram.com/embed.js") == 1
    assert "tsd-sitepost" not in main


def test_the_posts_never_become_the_share_image(monkeypatch):
    html, _ = _build(monkeypatch, {"posts": [_post(1), _post(2)], "extra_photos": {}})

    assert 'property="og:image" content="%s"' % HERO in html


def test_no_posts_means_an_unused_website_photo_stands_in(monkeypatch):
    """The article used the hero; the spare photo it never used fills the slot,
    inlined like every other photo on the page."""
    assets = {HERO: "data:image/jpeg;base64,SEVSTw==", SPARE: "data:image/jpeg;base64,U1BBUkU="}
    html, _ = _build(monkeypatch, {"posts": [], "extra_photos": {}}, assets=assets)
    main = _main(html)

    assert main.count('class="tsd-post tsd-sitepost"') == 1
    assert "data:image/jpeg;base64,U1BBUkU=" in main
    # The hero the article already shows is not shown twice.
    assert main.count("data:image/jpeg;base64,SEVSTw==") == 1


def test_one_post_and_one_website_photo_fill_both_slots(monkeypatch):
    extra = {"https://darkhorsecoffeeroasters.com/img/extra.jpg": "data:image/png;base64,RVhUUkE="}
    html, _ = _build(monkeypatch, {"posts": [_post(1)], "extra_photos": extra})
    main = _main(html)

    assert main.count('class="instagram-media"') == 1
    assert main.count('class="tsd-post tsd-sitepost"') == 1
    assert "data:image/png;base64,RVhUUkE=" in main


def test_a_website_photo_we_could_not_download_is_never_shown(monkeypatch):
    """No bytes means a hotlink, and a hotlink we already failed to fetch is a
    broken image on the prospect's own page."""
    html, _ = _build(monkeypatch, {"posts": [], "extra_photos": {}}, assets={})

    assert 'class="tsd-post' not in _main(html)


def test_get_listed_never_starts_the_post_job(monkeypatch):
    _, started = _build(monkeypatch, {"posts": [_post(1)], "extra_photos": {}}, offer="get_listed")
    assert started == []
