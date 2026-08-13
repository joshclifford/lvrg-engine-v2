"""Never build a site for a business whose page we did not actually read.

Two real leads got past the original guard on 2026-08-12:

    affiliatesolar.com  HTTP 401 -> 'Private Site &nbsp;'  (19 chars)
    solaricon.tech      HTTP 200 -> 'Coming Soon!'         (12 chars)

Both were handed to the model as though they were homepages, and the log line
still read "Direct fetch OK". The guard existed but tested `if not raw_text`,
which only catches a blank response — the same fabricated-site outcome as
familydentalcare.com (2026-08-04), reached through a different door.

Two independent holes, so two independent sets of assertions: the fetch must
reject a non-200, and the guard must reject a body too small to be a real page.
"""

import pytest

import intel


class FakeResponse:
    def __init__(self, status_code, text, url="https://example.com/"):
        self.status_code = status_code
        self.text = text
        # requests exposes the post-redirect URL here; fetch_site_content now
        # carries it out as `final_url` so photos resolve against the right host.
        self.url = url


def _no_firecrawl(monkeypatch):
    """Force the direct-fetch fallback, which is the path under test."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")


# --------------------------------------------------------------------------
# 1. the fetch must not treat an HTTP error body as page content
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 404, 410, 429, 500, 502, 503])
def test_non_200_yields_no_content(monkeypatch, status):
    _no_firecrawl(monkeypatch)
    body = "<html><body>Private Site. This site is currently private.</body></html>"
    monkeypatch.setattr(intel.requests, "get",
                        lambda *a, **k: FakeResponse(status, body))

    out = intel.fetch_site_content("example.com")

    assert out["text"] == "", f"HTTP {status} body was accepted as site content"
    assert out["html"] == ""


def test_200_still_returns_content(monkeypatch):
    _no_firecrawl(monkeypatch)
    body = "<html><body>" + ("Real dental practice in San Diego. " * 40) + "</body></html>"
    monkeypatch.setattr(intel.requests, "get",
                        lambda *a, **k: FakeResponse(200, body))

    out = intel.fetch_site_content("example.com")

    assert len(out["text"]) > intel.MIN_SITE_TEXT_CHARS
    assert out["html"] == body


# --------------------------------------------------------------------------
# 2. the guard must reject a body too small to be a real page
# --------------------------------------------------------------------------

def test_threshold_is_sane():
    """Big enough to exclude a placeholder, small enough for a sparse one-pager."""
    assert 50 <= intel.MIN_SITE_TEXT_CHARS <= 2000


@pytest.mark.parametrize("text,label", [
    ("", "empty"),
    ("Private Site &nbsp;", "affiliatesolar.com HTTP 401"),
    ("Coming Soon!", "solaricon.tech parked page"),
    ("x" * (200 - 1), "one char under the floor"),
])
def test_guard_refuses_to_build_from_a_stub(monkeypatch, text, label):
    monkeypatch.setattr(intel, "fetch_site_content",
                        lambda domain: {"text": text, "html": ""})
    # If the guard leaks, this is what would have been called with a stub.
    monkeypatch.setattr(intel, "extract_intel_with_claude",
                        lambda *a, **k: pytest.fail(
                            f"guard leaked — model was called for {label!r}"))

    with pytest.raises(ValueError, match="Refusing to build"):
        intel.scrape_site("example.com")


def test_guard_allows_a_real_page(monkeypatch):
    """The floor must not block a genuine, if sparse, homepage."""
    real = "Acme Dental. Family dentistry in San Diego since 1998. " * 10
    assert len(real) >= intel.MIN_SITE_TEXT_CHARS

    monkeypatch.setattr(intel, "fetch_site_content",
                        lambda domain: {"text": real, "html": "<html></html>"})
    called = {}

    def fake_extract(*a, **k):
        called["yes"] = True
        return {}

    monkeypatch.setattr(intel, "extract_intel_with_claude", fake_extract)
    # v2 runs a press search here; v1 has no such stage. raising=False keeps
    # this file identical across both engines.
    monkeypatch.setattr(intel, "fetch_press_mentions", lambda *a, **k: [],
                        raising=False)

    intel.scrape_site("example.com")

    assert called.get("yes"), "a real page should have reached extraction"


# --------------------------------------------------------------------------
# 3. Firecrawl is not a way around either check
# --------------------------------------------------------------------------

def test_firecrawl_stub_falls_back_instead_of_being_trusted(monkeypatch):
    """Firecrawl renders login walls too — a stub must not short-circuit."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "fake-key")

    class FakeFirecrawl:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"markdown": "Private Site", "html": "<html></html>"}}

    monkeypatch.setattr(intel.requests, "post", lambda *a, **k: FakeFirecrawl())
    monkeypatch.setattr(intel.requests, "get",
                        lambda *a, **k: FakeResponse(401, "<html>nope</html>"))

    out = intel.fetch_site_content("example.com")

    assert out["text"] == "", "a 12-char Firecrawl stub was accepted as the site"
