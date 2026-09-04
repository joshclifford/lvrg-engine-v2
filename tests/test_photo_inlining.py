"""Photo rehosting — POD01-124.

The bug: the generator embedded the prospect's own image URLs verbatim, so a
published Smart Site kept fetching its photos from a server we do not control.
When Su Pan Bakery's site started answering 403, every image on our page went
blank — with no error, no failed status, and no alert. The build still said
"ready" and the lead magnet had already been sent.

These tests pin the two halves of the fix: intel downloads the bytes within a
bounded time and size budget, and every generator producer substitutes them
into its finished html.
"""

import inspect

import pytest

import generator
import intel


# --- bounds ------------------------------------------------------------------
#
# Same posture as test_bounds.py: assert the ceilings exist and stay sane, not
# that a particular number is right. The measured failure this guards is a
# serial fetch — four real photos took 19.8s that way, against a 160s caller
# abort on builds that already run 106-150s.

def test_only_the_photos_the_prompt_shows_are_fetched():
    """_build_photo_block slices photos[:4]; fetching more buys nothing."""
    assert intel._PHOTO_INLINE_MAX <= 4
    assert "photos[:4]" in inspect.getsource(generator._build_photo_block)


def test_per_photo_budgets_are_bounded():
    assert intel._PHOTO_FETCH_TIMEOUT <= 8
    assert intel._MAX_PHOTO_BYTES <= 1_000_000


def test_worst_case_fetch_fits_the_build_budget():
    """Parallel, so the wall clock is one photo's timeout, not the sum."""
    assert intel._PHOTO_FETCH_TIMEOUT <= 8
    assert "ThreadPoolExecutor" in inspect.getsource(intel.fetch_photo_assets)


def test_total_stays_under_the_preview_proxy_cap():
    """leadscraper's api/preview rejects an upstream over 5 MB with a 502 — a
    cap in another repo that would surface as a blank page, not a build error.
    base64 costs +33% on top of the raw bytes."""
    encoded = intel._MAX_PHOTO_TOTAL_BYTES * 4 / 3
    assert encoded + 100_000 < 5 * 1024 * 1024


# --- fetching ----------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, ctype="image/jpeg", body=b"\xff\xd8\xffhello", length=None):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self._body = body

    def iter_content(self, n):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, by_url):
    def fake_get(url, **kw):
        r = by_url.get(url)
        if r is None:
            raise RuntimeError("unreachable")
        return r
    monkeypatch.setattr(intel.requests, "get", fake_get)


def test_no_photos_costs_nothing(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("must not touch the network")
    monkeypatch.setattr(intel.requests, "get", explode)
    assert intel.fetch_photo_assets([]) == {}
    assert intel.fetch_photo_assets(None) == {}


def test_photo_becomes_a_data_uri(monkeypatch):
    url = "https://prospect.com/hero.jpg"
    _serve(monkeypatch, {url: _Resp()})
    out = intel.fetch_photo_assets([url])
    assert out[url].startswith("data:image/jpeg;base64,")


def test_unreachable_photo_keeps_its_original_url(monkeypatch):
    """The whole safety argument: a failed fetch degrades to today's behaviour
    for that photo, never to a failed build."""
    ok, dead = "https://p.com/a.jpg", "https://p.com/b.jpg"
    _serve(monkeypatch, {ok: _Resp()})
    out = intel.fetch_photo_assets([ok, dead])
    assert ok in out and dead not in out


def test_html_error_page_is_not_inlined_as_an_image(monkeypatch):
    """A soft-404 or WAF challenge answers 200 with text/html. Inlining that
    would publish a broken icon that looks exactly like the original bug."""
    url = "https://p.com/a.jpg"
    _serve(monkeypatch, {url: _Resp(ctype="text/html", body=b"<html>nope</html>")})
    assert intel.fetch_photo_assets([url]) == {}


def test_oversize_photo_is_rejected_on_real_bytes(monkeypatch):
    """Content-Length is a hint, not a promise — a lying header must not get
    past the cap."""
    url = "https://p.com/big.jpg"
    body = b"x" * (intel._MAX_PHOTO_BYTES + 1)
    _serve(monkeypatch, {url: _Resp(body=body, length=10)})
    assert intel.fetch_photo_assets([url]) == {}


def test_non_200_is_rejected(monkeypatch):
    url = "https://p.com/a.jpg"
    _serve(monkeypatch, {url: _Resp(status=403)})
    assert intel.fetch_photo_assets([url]) == {}


def test_only_the_first_n_are_fetched(monkeypatch):
    urls = [f"https://p.com/{i}.jpg" for i in range(6)]
    _serve(monkeypatch, {u: _Resp() for u in urls})
    out = intel.fetch_photo_assets(urls)
    assert len(out) == intel._PHOTO_INLINE_MAX


def test_total_budget_drops_later_photos_not_the_hero(monkeypatch):
    """photos[0] is the hero and the one the page leans on hardest, so the
    total-bytes trim must run in the original order, not completion order."""
    half = intel._MAX_PHOTO_TOTAL_BYTES // 2 + 1
    urls = [f"https://p.com/{i}.jpg" for i in range(3)]
    monkeypatch.setattr(intel, "_MAX_PHOTO_BYTES", half + 10)
    _serve(monkeypatch, {u: _Resp(body=b"y" * half) for u in urls})
    out = intel.fetch_photo_assets(urls)
    assert urls[0] in out, "hero was dropped"
    assert urls[2] not in out, "budget not enforced"


# --- substitution ------------------------------------------------------------

def test_urls_are_replaced_in_the_html():
    url = "https://p.com/a.jpg?v=1&width=1000"
    html = f'<img src="{url}">'
    out = generator._inline_photo_assets(html, {url: "data:image/jpeg;base64,AAA"})
    assert url not in out
    assert "data:image/jpeg;base64,AAA" in out


def test_substitution_is_idempotent():
    url = "https://p.com/a.jpg"
    assets = {url: "data:image/jpeg;base64,AAA"}
    once = generator._inline_photo_assets(f'<img src="{url}">', assets)
    assert generator._inline_photo_assets(once, assets) == once


def test_a_page_only_carries_the_photos_it_uses():
    """A sub-page using two of four photos must not carry the other two's
    bytes — that is what keeps multi-page sites off the proxy's size cap."""
    used, unused = "https://p.com/a.jpg", "https://p.com/b.jpg"
    assets = {used: "data:image/jpeg;base64,AAA",
              unused: "data:image/jpeg;base64,BBB"}
    out = generator._inline_photo_assets(f'<img src="{used}">', assets)
    assert "AAA" in out and "BBB" not in out


def test_missing_assets_leave_the_html_alone():
    html = '<img src="https://p.com/a.jpg">'
    assert generator._inline_photo_assets(html, {}) == html
    assert generator._inline_photo_assets(html, None) == html


# --- wiring ------------------------------------------------------------------
#
# A producer that forgets the substitution ships the original bug, silently and
# invisibly, exactly as before. Pin every one of them.

@pytest.mark.parametrize("producer", [
    "generate_site",
    "generate_page",
    "generate_offer_lead_magnet_page",
])
def test_every_producer_inlines_its_photos(producer):
    src = inspect.getsource(getattr(generator, producer))
    assert "_inline_photo_assets(html, photo_assets)" in src, \
        f"{producer} publishes html without rehosting its photos"


def test_scrape_site_fetches_and_publishes_the_assets():
    src = inspect.getsource(intel.scrape_site)
    assert "fetch_photo_assets(photos)" in src
    assert '"photo_assets": photo_assets' in src


# --- undownloadable photos fall back to their url -----------------------------
#
# A failed download means "we could not fetch it", not "it is dead". The engine
# fetches from a Railway datacenter IP, and blanket datacenter blocking is the
# commonest cause — the same failure that killed the old Yelp photo source. An
# oversize or slow photo loads fine in a real browser too. So a photo we could
# not download still gets offered with its original url: weaker than rehosting,
# no worse than every build before rehosting existed.

def test_downloaded_photos_are_rehosted_and_the_rest_still_hotlink():
    got, missed = "https://p.com/a.jpg", "https://p.com/b.jpg"
    assets = {got: "data:image/jpeg;base64,AAA"}
    block = generator._build_photo_block({"photos": [got, missed]})
    assert got in block and missed in block, "a photo was dropped"

    html = generator._inline_photo_assets(
        f'<img src="{got}"><img src="{missed}">', assets)
    assert got not in html, "downloaded photo was not rehosted"
    assert missed in html, "undownloaded photo lost its fallback url"


def test_a_total_fetch_failure_still_offers_the_photos():
    """Every build before rehosting shipped exactly this. No regression."""
    intel_d = {"photos": ["https://p.com/a.jpg"], "photo_assets": {}}
    assert "a.jpg" in generator._build_photo_block(intel_d)


def test_gradients_still_cover_the_no_photos_case():
    """The fallback that always existed: nothing found on the page at all."""
    block = generator._build_photo_block({"photos": [], "photo_assets": {}})
    assert "No real photos available" in block


def test_a_caller_that_never_fetched_keeps_its_photos():
    intel_d = {"photos": ["https://p.com/a.jpg"]}
    assert "a.jpg" in generator._build_photo_block(intel_d)


# --- map tiles are not photos -------------------------------------------------
#
# panchitasbakery.com embeds a Leaflet/OSM map. Its tiles are real .png files
# with innocent paths, so they cleared every filter and outranked the food
# photos on DOM position — the generator got six map tiles labelled "REAL
# PHOTOS" and built the hero and the location gallery out of them. OSM then
# served its black-and-yellow "Access blocked" image, with HTTP 200 and
# Content-Type: image/png, so nothing downstream could tell it from a photo.

TILES = [
    "https://a.tile.openstreetmap.org/16/11446/26452.png",
    "https://b.tile.openstreetmap.org/16/11445/26452.png",
    "https://api.mapbox.com/v4/mapbox.streets/16/11446/26452.png",
    "https://basemaps.cartocdn.com/light_all/16/11446/26452.png",
    "https://selfhosted.example.com/tiles/16/11446/26452.png",
]

REAL_PHOTOS = [
    "https://panchitasbakery.com/wp-content/uploads/2025/12/DSC06907.jpg",
    "https://shop.com/cdn/shop/files/3.jpg?v=1743618795&width=1000",
    "https://p.com/images/storefront.png",
    "https://images.squarespace-cdn.com/content/abc/hero.jpg",
]


@pytest.mark.parametrize("url", TILES)
def test_map_tiles_are_recognised(url):
    assert intel._is_map_tile(url), url


@pytest.mark.parametrize("url", REAL_PHOTOS)
def test_real_photos_are_not_mistaken_for_tiles(url):
    assert not intel._is_map_tile(url), url


def test_wordpress_date_folders_are_not_read_as_tile_coordinates():
    """/uploads/2025/12/1234.jpg has the same three-numeric-segment shape as
    /{z}/{x}/{y}.png. Capping z at two digits is what separates them."""
    assert not intel._is_map_tile("https://p.com/wp-content/uploads/2025/12/1234.jpg")


def test_tiles_are_dropped_before_they_can_become_photos():
    html = "".join(f'<img src="{u}">' for u in TILES[:2]) + \
           '<img src="https://p.com/img/storefront.jpg">'
    photos = intel.extract_photos(html, "https://p.com")
    assert photos == ["https://p.com/img/storefront.jpg"]


def test_leaflet_map_furniture_is_dropped():
    """Once the tiles are gone, the library's own pins are what is left to
    become the hero."""
    html = ('<img src="https://static.spotapps.co/web-lib/leaflet/dist/images/marker-shadow.png">'
            '<img src="https://p.com/img/storefront.jpg">')
    assert intel.extract_photos(html, "https://p.com") == ["https://p.com/img/storefront.jpg"]


# A numerically-sharded CMS path has the same three-segment shape as an XYZ
# tile. Shape alone dropped real photos; the 2**z bounds rule is what tells
# them apart, because a genuine tile's x and y always fit its zoom's grid.
NOT_TILES = [
    "https://p.com/media/12/34/5678.jpg",    # y=5678 does not fit z=12's 4096
    "https://p.com/img/2/3/4.jpg",           # y=4 does not fit z=2's 4
    "https://p.com/wp-content/uploads/2025/12/1234.jpg",
    "https://cdn.shopify.com/s/files/1/0234/5678/products/hero.jpg",
    # Bare "marker"/"leaflet" were briefly junk words and cost these two.
    "https://p.com/images/marker-storefront.jpg",
    "https://p.com/leaflet-design-samples/portfolio-1.jpg",
]


@pytest.mark.parametrize("url", NOT_TILES)
def test_ordinary_photos_are_not_read_as_tiles(url):
    assert not intel._is_map_tile(url), url


@pytest.mark.parametrize("url", NOT_TILES)
def test_and_they_survive_extraction(url):
    assert intel.extract_photos(f'<img src="{url}">', "https://p.com") == [url]


def test_a_tile_beyond_max_zoom_is_not_a_tile():
    """No slippy scheme goes past z=22; three big numbers are just a path."""
    assert not intel._is_map_tile("https://p.com/99/11446/26452.png")


def test_a_page_that_is_only_a_map_yields_no_photos():
    """Gradients, not an 'Access blocked' hero."""
    html = "".join(f'<img src="{u}">' for u in TILES)
    assert intel.extract_photos(html, "https://p.com") == []


# --- the blobs must never reach the intel RECORD ------------------------------
#
# The defect PR #11's review caught. photo_assets started life on the dict
# scrape_site returns, and that dict does not stay in this process: api.py
# streams it as sse("intel"), puts it in result_payload, and leadscraper writes
# it to `businesses.smart_site_intel` — a column its All Leads query selects for
# every row on screen. Measured with four real photos: one lead's intel went
# from 6.7 KB to 771 KB, and a 25-row page from 166 KB to 18.8 MB. Nothing
# errored; builds succeeded and photos displayed. It would only ever have shown
# up as an All Leads page that got slower with every build.
#
# leadSelect.ts's own header records the last time that column class was let
# grow — a select that "weighed 62 kB per page, 78% of the tab's transfer".

def test_the_generator_takes_assets_not_the_intel_dict():
    """Signature-level: the helper cannot reach into intel even by accident."""
    sig = inspect.signature(generator._inline_photo_assets)
    assert list(sig.parameters) == ["html", "assets"]


@pytest.mark.parametrize("producer", [
    "generate_site",
    "generate_page",
    "generate_offer_lead_magnet_page",
    "generate_multi_page_site",
])
def test_producers_accept_photo_assets_explicitly(producer):
    sig = inspect.signature(getattr(generator, producer))
    assert "photo_assets" in sig.parameters, \
        f"{producer} cannot be given the bytes without smuggling them on intel"


def test_the_pipeline_lifts_the_assets_off_intel_before_emitting_it():
    """api.py must pop BEFORE sse("intel") and before result_payload."""
    import api
    src = inspect.getsource(api.run_pipeline)
    pop = src.find('intel.pop("photo_assets"')
    emit = src.find('sse("intel"')
    assert pop > -1, "api.py never lifts the blobs off the intel record"
    assert emit > -1
    assert pop < emit, "intel is emitted with the photo bytes still on it"


def test_the_saved_intel_file_excludes_the_blobs():
    src = inspect.getsource(intel.scrape_site)
    dump = src[src.find("json.dump"):src.find("json.dump") + 200]
    assert 'k != "photo_assets"' in dump, \
        "the on-disk intel record would carry hundreds of KB of base64"
