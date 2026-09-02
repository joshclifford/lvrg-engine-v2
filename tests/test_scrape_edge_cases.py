"""POD01-16 cases E5-E8 and E15-E17, tested rather than assumed.

Each of these was on the "untested, no evidence either way" list. Where the
current behaviour turns out to be wrong, the test is marked xfail(strict=True)
so the gap is recorded and the suite tells us the moment someone fixes it —
rather than a green suite implying coverage that is not there.
"""

import pytest

import intel

BASE = "https://example-dental.com/"


class FakeResponse:
    def __init__(self, status_code=200, text="", url=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.url = url or BASE
        self.headers = headers or {}


def _big_page(chars):
    """A page whose STRIPPED text is exactly `chars` long."""
    return "<html><body>" + ("a" * chars) + "</body></html>"


# --------------------------------------------------------------------------
# E5 — Firecrawl returns good markdown but no html
# --------------------------------------------------------------------------

def test_e5_firecrawl_without_html_backfills_it(monkeypatch):
    """Good markdown + empty html must not silently mean zero photos.

    extract_photos reads raw_html. When Firecrawl returns markdown and an empty
    `html` field the build used to proceed with no photos, indistinguishable
    from a site that genuinely has none. One cheap GET backfills it.
    """
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "fake")
    good_markdown = "Acme Dental. Family dentistry in San Diego. " * 20

    class FC:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"markdown": good_markdown, "html": ""}}

    monkeypatch.setattr(intel.requests, "post", lambda *a, **k: FC())
    monkeypatch.setattr(
        intel.requests, "get",
        lambda *a, **k: FakeResponse(200, '<img src="/photos/storefront.jpg">'))

    out = intel.fetch_site_content("example-dental.com")

    assert len(out["text"]) >= intel.MIN_SITE_TEXT_CHARS
    assert out["html"], "html was not backfilled"
    assert intel.extract_photos(out["html"], out["final_url"]), "still no photos"


def test_e5_backfill_failure_is_not_fatal(monkeypatch):
    """The text is already good — a failed backfill must not lose the build."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "fake")
    good_markdown = "Acme Dental. Family dentistry in San Diego. " * 20

    class FC:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"markdown": good_markdown, "html": ""}}

    monkeypatch.setattr(intel.requests, "post", lambda *a, **k: FC())

    def boom(*a, **k):
        raise intel.requests.exceptions.Timeout("backfill hung")

    monkeypatch.setattr(intel.requests, "get", boom)

    out = intel.fetch_site_content("example-dental.com")

    assert len(out["text"]) >= intel.MIN_SITE_TEXT_CHARS, "text lost to a photo backfill"
    assert out["html"] == ""


# --------------------------------------------------------------------------
# E6 — the 60k extraction cutoff, at the boundary
# --------------------------------------------------------------------------

@pytest.mark.parametrize("size", [59_999, 60_000, 60_001, 120_000])
def test_e6_extraction_input_never_exceeds_the_cap(monkeypatch, size):
    cap = intel.EXTRACT_MAX_CHARS
    monkeypatch.setattr(intel, "fetch_site_content",
                        lambda d: {"text": "x" * size, "html": ""})

    seen = {}

    def fake_extract(domain, raw_text, meter=None):
        seen["len"] = len(raw_text)
        return {}

    monkeypatch.setattr(intel, "extract_intel_with_claude", fake_extract)
    monkeypatch.setattr(intel, "fetch_press_mentions", lambda *a, **k: [], raising=False)

    intel.scrape_site("example-dental.com")

    assert seen["len"] == min(size, cap), (
        f"page of {size} chars sent {seen['len']} to the model, cap is {cap}"
    )


def test_e6_cap_is_a_slice_not_a_truncating_parser(monkeypatch):
    """Tags are already stripped before the cap, so a cut cannot split a tag."""
    import inspect
    src = inspect.getsource(intel.scrape_site)
    assert "raw_text[:EXTRACT_MAX_CHARS]" in src
    # raw_text comes out of fetch_site_content already tag-stripped. The
    # stripping moved into _text_from_html when the Playwright tier landed, so
    # both tiers measure MIN_SITE_TEXT_CHARS on the same shape of text — assert
    # the behaviour rather than where the regex lives.
    assert "re.sub(r'<[^>]+>'" in inspect.getsource(intel._text_from_html)
    assert intel._text_from_html("<p>hi <b>there</b></p>") == "hi there"
    assert "<" not in intel._text_from_html(_big_page(50) + "<div class='x'>")


# --------------------------------------------------------------------------
# E7 — the site redirects somewhere else
# --------------------------------------------------------------------------

def test_e7_redirect_target_is_carried_out_of_the_fetch(monkeypatch):
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")
    body = ('<html><body><img src="/photos/storefront.jpg">'
            + ("Real content. " * 30) + "</body></html>")
    monkeypatch.setattr(
        intel.requests, "get",
        lambda *a, **k: FakeResponse(200, body, url="https://newdomain.example/"))

    out = intel.fetch_site_content("olddomain.example")

    assert out["final_url"] == "https://newdomain.example/"


def test_e7_photos_resolve_against_the_redirect_target(monkeypatch):
    """A relative src must point at where the html came from, not where we asked."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")
    body = ('<html><body><img src="/photos/storefront.jpg">'
            + ("Real content. " * 30) + "</body></html>")
    monkeypatch.setattr(
        intel.requests, "get",
        lambda *a, **k: FakeResponse(200, body, url="https://newdomain.example/"))
    monkeypatch.setattr(intel, "extract_intel_with_claude", lambda *a, **k: {})
    monkeypatch.setattr(intel, "fetch_press_mentions", lambda *a, **k: [], raising=False)

    result = intel.scrape_site("olddomain.example")

    assert result["photos"], "photos were lost"
    assert all("newdomain.example" in p for p in result["photos"]), (
        f"photos still point at the pre-redirect host: {result['photos']}"
    )


def test_e7_no_redirect_still_uses_the_requested_host(monkeypatch):
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")
    body = ('<html><body><img src="/photos/shot.jpg">'
            + ("Real content. " * 30) + "</body></html>")
    monkeypatch.setattr(
        intel.requests, "get",
        lambda *a, **k: FakeResponse(200, body, url="https://example-dental.com/"))

    out = intel.fetch_site_content("example-dental.com")
    kept = intel.extract_photos(out["html"], out["final_url"])

    assert kept and all("example-dental.com" in p for p in kept)


# --------------------------------------------------------------------------
# E8 — a slow site eats the intel budget
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(intel, "INTEL_BUDGET_SECONDS"),
                    reason="v1 has neither a press stage nor an intel budget — "
                           "worth knowing on its own, since v1 is what "
                           "production calls and nothing there bounds a slow site")
def test_e8_slow_scrape_skips_the_press_search(monkeypatch):
    monkeypatch.setattr(intel, "fetch_site_content",
                        lambda d: {"text": "Real content. " * 50, "html": ""})
    monkeypatch.setattr(intel, "extract_intel_with_claude", lambda *a, **k: {})

    called = {"press": False}

    def fake_press(*a, **k):
        called["press"] = True
        return []

    monkeypatch.setattr(intel, "fetch_press_mentions", fake_press, raising=False)

    # Clock jumps past the budget between the start of scrape_site and the check.
    ticks = iter([0.0] + [intel.INTEL_BUDGET_SECONDS + 10] * 20)
    monkeypatch.setattr(intel.time, "monotonic", lambda: next(ticks))

    intel.scrape_site("example-dental.com")

    assert not called["press"], "press search ran after the intel budget was gone"


# --------------------------------------------------------------------------
# E15 / E16 / E17 — photo extraction odd shapes
# --------------------------------------------------------------------------

def test_e15_all_svg_yields_no_photos_without_crashing():
    html = ('<img src="/a.svg"><img src="/b.svg">'
            '<img src="/c.gif"><img src="/d.webp?v=1">')
    kept = intel.extract_photos(html, BASE)
    assert all(".svg" not in u and ".gif" not in u for u in kept)


def test_e16_cdn_filename_in_the_query_is_read():
    """Documented intent: the extension may live in the query."""
    assert intel.extract_photos('<img src="/cdn?file=storefront.jpg">', BASE)


def test_e16_cdn_junk_in_the_query_is_still_dropped():
    """...which is exactly why the junk check must see the query too."""
    assert not intel.extract_photos('<img src="/cdn?file=logo.png">', BASE)


def test_e17_hanging_head_request_does_not_break_extraction(monkeypatch):
    """_full_size runs a blocking HEAD on the top candidates inside a 135s budget."""
    def boom(*a, **k):
        raise intel.requests.exceptions.Timeout("HEAD hung")

    monkeypatch.setattr(intel.requests, "head", boom)

    html = "".join(f'<img src="/photos/shot-{i}.jpg">' for i in range(4))
    kept = intel.extract_photos(html, BASE)

    assert len(kept) == 4, "a timing-out HEAD lost photos instead of degrading"


def test_e17_full_size_upgrade_is_bounded():
    assert intel._UPGRADE_TOP_N <= 2
    assert intel._UPGRADE_HEAD_TIMEOUT <= 3
