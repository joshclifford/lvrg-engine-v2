"""POD01-17 — the Playwright rendering tier.

The site-builder used to fall back to a raw `requests.get`, which returns the
page as SHIPPED rather than as RENDERED. On a Next.js or SPA prospect that is an
empty shell: on 149 real lead domains, 11 came back under MIN_SITE_TEXT_CHARS on
that path and refused to build, and 19 more read fine but produced zero photos.

These tests drive `_render_with_playwright` against a FAKE driver. Nothing here
starts a browser — the point is the control flow around it (status floor,
budget, fallback, cleanup), which is where a build-breaking bug would live.
`tests/conftest.py` disables the tier everywhere else for the same reason.
"""

import sys
import threading
import time
import types

import pytest

import intel

pytestmark = pytest.mark.playwright_allowed

URL = "https://example-dental.com"


# --------------------------------------------------------------------------
# A fake playwright, injected as the real module so the lazy import finds it.
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    def __init__(self, driver):
        self.driver = driver
        self.url = URL + "/rendered"

    def set_default_timeout(self, ms):
        self.driver.events.append(("timeout", ms))

    def goto(self, url, wait_until=None, timeout=None):
        self.driver.events.append(("goto", url))
        if self.driver.goto_raises:
            raise self.driver.goto_raises
        return self.driver.response

    def wait_for_load_state(self, state, timeout=None):
        self.driver.events.append(("wait", state))

    def evaluate(self, js):
        self.driver.events.append(("evaluate", js[:20]))
        if self.driver.evaluate_raises:
            raise self.driver.evaluate_raises
        return 3

    def wait_for_timeout(self, ms):
        self.driver.events.append(("settle", ms))

    def content(self):
        self.driver.events.append(("read", "content"))
        return self.driver.html

    def inner_text(self, selector):
        self.driver.events.append(("read", "text"))
        return self.driver.text


class FakeContext:
    def __init__(self, driver):
        self.driver = driver

    def new_page(self):
        return FakePage(self.driver)


class FakeBrowser:
    def __init__(self, driver):
        self.driver = driver

    def new_context(self, **kw):
        self.driver.context_kwargs = kw
        return FakeContext(self.driver)

    def close(self):
        self.driver.events.append(("browser.close", None))
        if self.driver.close_raises:
            raise self.driver.close_raises


class FakeChromium:
    def __init__(self, driver):
        self.driver = driver

    def launch(self, headless=None, args=None):
        self.driver.events.append(("launch", tuple(args or ())))
        if self.driver.launch_raises:
            raise self.driver.launch_raises
        return FakeBrowser(self.driver)


class FakePlaywright:
    def __init__(self, driver):
        self.driver = driver
        self.chromium = FakeChromium(driver)

    def stop(self):
        self.driver.events.append(("pw.stop", None))


class Driver:
    """Everything the fake browser should do, in one knob-bag."""

    def __init__(self, *, status=200, html="<html></html>", text="rendered text",
                 launch_raises=None, goto_raises=None, evaluate_raises=None,
                 close_raises=None, response="default"):
        self.status = status
        self.html = html
        self.text = text
        self.launch_raises = launch_raises
        self.goto_raises = goto_raises
        self.evaluate_raises = evaluate_raises
        self.close_raises = close_raises
        self.response = FakeResponse(status) if response == "default" else response
        self.events = []
        self.context_kwargs = None

    def start(self):
        self.events.append(("pw.start", None))
        return FakePlaywright(self)


@pytest.fixture
def driver(monkeypatch):
    """Install a fake `playwright.sync_api` for the duration of one test."""
    def install(d):
        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: d
        package = types.ModuleType("playwright")
        package.sync_api = module
        monkeypatch.setitem(sys.modules, "playwright", package)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
        monkeypatch.setattr(intel, "PLAYWRIGHT_ENABLED", True)
        return d
    return install


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------

def test_renders_and_returns_text_html_and_final_url(driver):
    d = driver(Driver(html="<html><body><p>lots   of\n text</p>"
                           "<img src='/a.jpg'></body></html>"))

    text, html, final_url = intel._render_with_playwright(URL)

    assert text == "lots of text", "whitespace was not collapsed like the fetch path"
    assert "<img src='/a.jpg'>" in html
    # page.url, not the requested URL: a prospect who redirects must have their
    # relative image paths resolved against where the page ACTUALLY came from —
    # the same defect fixed for the fetch tier in E7.
    assert final_url == URL + "/rendered"


def test_text_comes_from_the_dom_not_from_inner_text(driver):
    """inner_text returns only what is VISIBLE.

    Measured on the benchmark sites it was a third of the page: acculynx.com
    gave 6,156 chars against 17,947 from stripping the rendered DOM — and the
    direct fetch it replaces already produced 16,301. Rendering a page is
    supposed to add content; a tier that reads less than the raw fetch it
    replaced would be a regression wearing a browser.
    """
    d = driver(Driver(html="<body><p>visible</p>"
                           "<div hidden>hidden but real</div></body>",
                      text="visible"))

    text, _, _ = intel._render_with_playwright(URL)

    assert "hidden but real" in text, "read only the visible text — content lost"


def test_both_tiers_measure_text_the_same_way():
    """MIN_SITE_TEXT_CHARS gates the build and EXTRACT_MAX_CHARS gates the
    prompt; both are counted on whatever the tier returned. Two strippers would
    mean one tier's 200 chars was not the other's."""
    import inspect
    assert "_text_from_html" in inspect.getsource(intel._render_with_playwright)
    assert "_text_from_html" in inspect.getsource(intel.fetch_site_content)


def test_scrolls_before_reading_so_lazy_images_load(driver):
    d = driver(Driver())

    intel._render_with_playwright(URL)

    kinds = [e[0] for e in d.events]
    assert "evaluate" in kinds, "never scrolled — lazy-loaded photos stay unloaded"
    assert "settle" in kinds, "no settle — images were triggered but never fetched"
    # Order is the whole point: scroll, wait, THEN read. Reading first captures
    # the placeholder src attributes the scroll existed to replace.
    assert kinds.index("evaluate") < kinds.index("settle") < kinds.index("read")


def test_lazy_attributes_are_promoted_before_the_html_is_read(driver):
    """Scrolling fires viewport-triggered loaders; plenty of themes instead park
    the real URL in data-src and swap it on a carousel tick we never trigger."""
    d = driver(Driver())

    intel._render_with_playwright(URL)

    scripts = [e[1] for e in d.events if e[0] == "evaluate"]
    assert len(scripts) == 2, "expected a scroll pass and a lazy-promotion pass"
    reads = [i for i, e in enumerate(d.events) if e[0] == "read"]
    evals = [i for i, e in enumerate(d.events) if e[0] == "evaluate"]
    assert max(evals) < min(reads), "promoted lazy refs after reading the html"


def test_container_safe_launch_flags(driver):
    """Railway runs as root in a container with a 64MB /dev/shm.

    Without --no-sandbox Chromium refuses to start; without
    --disable-dev-shm-usage it crashes partway through a render. Both read as a
    random scrape failure in the logs.
    """
    d = driver(Driver())

    intel._render_with_playwright(URL)

    args = next(e[1] for e in d.events if e[0] == "launch")
    assert "--no-sandbox" in args
    assert "--disable-dev-shm-usage" in args


def test_uses_the_same_user_agent_as_the_fetch_path(driver):
    d = driver(Driver())

    intel._render_with_playwright(URL)

    assert d.context_kwargs["user_agent"] == intel.HEADERS["User-Agent"]


# --------------------------------------------------------------------------
# The status floor — POD01-15's P1, carried into this tier
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_non_200_is_refused(driver, status):
    """A browser renders a login wall as happily as a homepage.

    affiliatesolar.com (HTTP 401, body 'Private Site') is the lead that proved
    it on the fetch path: Claude invented an entire solar company from 19 chars
    and published it under a real business's name. Rendering that same page
    prettier does not make it the prospect's site.
    """
    d = driver(Driver(status=status, text="Private Site", html="<p>Private Site</p>"))

    text, html, final_url = intel._render_with_playwright(URL)

    assert (text, html) == ("", ""), f"HTTP {status} was accepted as prospect content"
    assert final_url == URL


def test_missing_response_object_is_refused(driver):
    """page.goto returns None for a navigation that never produced a response."""
    d = driver(Driver(response=None))

    assert intel._render_with_playwright(URL) == ("", "", URL)


# --------------------------------------------------------------------------
# Failure must always fall back, never raise
# --------------------------------------------------------------------------

def test_launch_failure_falls_back_instead_of_raising(driver):
    d = driver(Driver(launch_raises=RuntimeError("Executable doesn't exist")))

    assert intel._render_with_playwright(URL) == ("", "", URL)


def test_navigation_timeout_falls_back_instead_of_raising(driver):
    d = driver(Driver(goto_raises=TimeoutError("Timeout 20000ms exceeded")))

    assert intel._render_with_playwright(URL) == ("", "", URL)


def test_missing_playwright_package_falls_back(monkeypatch):
    """The deploy-shaped failure: package or browser never made it into the image."""
    monkeypatch.setattr(intel, "PLAYWRIGHT_ENABLED", True)
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    assert intel._render_with_playwright(URL) == ("", "", URL)


def test_a_failed_scroll_still_returns_the_rendered_page(driver):
    """A page that blocks our script still gave us a rendered DOM."""
    d = driver(Driver(evaluate_raises=RuntimeError("CSP blocked eval"),
                      html="<body>real rendered copy<img src='/a.jpg'></body>"))

    text, html, _ = intel._render_with_playwright(URL)

    assert text == "real rendered copy"
    assert "<img src='/a.jpg'>" in html


# --------------------------------------------------------------------------
# Cleanup — a leaked Chromium outlives the request and eats the container
# --------------------------------------------------------------------------

def test_browser_is_closed_on_the_happy_path(driver):
    d = driver(Driver())

    intel._render_with_playwright(URL)

    kinds = [e[0] for e in d.events]
    assert "browser.close" in kinds
    assert "pw.stop" in kinds


def test_browser_is_closed_when_the_render_raises(driver):
    d = driver(Driver(goto_raises=RuntimeError("navigation crashed")))

    intel._render_with_playwright(URL)

    kinds = [e[0] for e in d.events]
    assert "browser.close" in kinds, "browser leaked on the error path"
    assert "pw.stop" in kinds


def test_a_failing_close_still_stops_the_driver(driver):
    """close() on an already-crashed browser raises; pw.stop() must still run."""
    d = driver(Driver(close_raises=RuntimeError("browser already gone")))

    intel._render_with_playwright(URL)

    assert ("pw.stop", None) in d.events


# --------------------------------------------------------------------------
# Time budget
# --------------------------------------------------------------------------

def test_disabled_by_env_flag_returns_immediately(monkeypatch):
    monkeypatch.setattr(intel, "PLAYWRIGHT_ENABLED", False)

    assert intel._render_with_playwright(URL) == ("", "", URL)


def test_skipped_when_too_little_budget_remains(driver):
    """Starting a browser that cannot finish wastes the seconds the fetch
    fallback needs. Chromium cold start alone is 1-2s."""
    d = driver(Driver())

    assert intel._render_with_playwright(URL, budget_seconds=2) == ("", "", URL)
    assert d.events == [], "a browser was started with no budget to use it"


def test_budget_caps_the_page_timeout(driver):
    """The per-page ceiling is the SMALLER of the configured timeout and what is
    left of the whole-scrape budget."""
    d = driver(Driver())

    intel._render_with_playwright(URL, budget_seconds=8)

    ms = next(e[1] for e in d.events if e[0] == "timeout")
    assert ms <= 8000, f"page timeout {ms}ms exceeds the {8000}ms left in the budget"


# --------------------------------------------------------------------------
# The tier chain — Firecrawl, then Playwright, then the raw fetch
# --------------------------------------------------------------------------

class FakeHTTPResponse:
    def __init__(self, status_code=200, text="", url=URL + "/", headers=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}


def _firecrawl(monkeypatch, markdown, html):
    class FC:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"markdown": markdown, "html": html}}

    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "fake")
    monkeypatch.setattr(intel.requests, "post", lambda *a, **k: FC())


def _fake_render(monkeypatch, text="", html="", url=URL, record=None):
    def render(u, budget_seconds=None):
        if record is not None:
            record.append((u, budget_seconds))
        return text, html, url
    monkeypatch.setattr(intel, "_render_with_playwright", render)


def test_playwright_replaces_the_fetch_fallback_when_firecrawl_is_down(monkeypatch):
    """The headline change: an SPA prospect whose raw HTML is an empty shell.

    texascreative.com returns 17 chars of text to `requests.get` — below
    MIN_SITE_TEXT_CHARS, so scrape_site refuses to build at all.
    """
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")
    shell = "<html><body><div id='root'></div></body></html>"
    monkeypatch.setattr(intel.requests, "get",
                        lambda *a, **k: FakeHTTPResponse(200, shell))
    rendered = "Texas Creative — full service agency. " * 20
    _fake_render(monkeypatch, text=rendered, html="<img src='/team.jpg'>")

    out = intel.fetch_site_content("texascreative.com")

    assert out["text"] == rendered, "fell through to the unrendered shell"
    assert len(out["text"]) >= intel.MIN_SITE_TEXT_CHARS
    assert intel.extract_photos(out["html"], out["final_url"])


def test_falls_through_to_direct_fetch_when_playwright_comes_back_empty(monkeypatch):
    """A browser failure must degrade to the old path, never fail the build."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")
    body = "<html><body>" + ("Real server rendered copy. " * 40) + "</body></html>"
    monkeypatch.setattr(intel.requests, "get",
                        lambda *a, **k: FakeHTTPResponse(200, body))
    _fake_render(monkeypatch, text="", html="")

    out = intel.fetch_site_content("example-dental.com")

    assert "Real server rendered copy." in out["text"]


def test_playwright_recovers_photos_when_firecrawl_returns_none(monkeypatch):
    """Firecrawl reads the TEXT of a JS site fine and still returns zero photos —
    measured on 3 of the 6 benchmark sites. Its html is never scrolled, so the
    <img> tags are all placeholders and the build ships on gradients."""
    good = ("Asian Bay. Vietnamese kitchen in Paris. " * 20).strip()
    _firecrawl(monkeypatch, good, "<img src='data:image/gif;base64,R0lGOD'>")
    _fake_render(monkeypatch, text=good, html="<img src='/gallery/pho.jpg'>",
                 url=URL + "/")

    out = intel.fetch_site_content("asianbay92.fr")

    assert out["text"] == good, "Firecrawl's text was discarded"
    assert intel.extract_photos(out["html"], out["final_url"]), \
        "photos still zero — the re-render did not take"


def test_firecrawl_html_with_photos_is_left_alone(monkeypatch):
    """No browser when there is nothing to gain — it costs seconds and memory."""
    good = "Maggies Cafe. Breakfast all day. " * 20
    _firecrawl(monkeypatch, good, "<img src='/photos/storefront.jpg'>")
    calls = []
    _fake_render(monkeypatch, record=calls)

    out = intel.fetch_site_content("www.maggiescafe2014.com")

    assert calls == [], "started a browser for a page that already had photos"
    assert intel.extract_photos(out["html"], out["final_url"])


def test_photo_rerender_never_costs_the_text(monkeypatch):
    """If the re-render fails, keep what Firecrawl already gave us."""
    good = ("Sista Place. Coffee and live music. " * 20).strip()
    _firecrawl(monkeypatch, good, "<img src='data:image/gif;base64,R0lGOD'>")
    _fake_render(monkeypatch, text="", html="")

    out = intel.fetch_site_content("www.sistaplace.com")

    assert out["text"] == good, "a failed photo re-render lost the build"


def test_render_is_given_the_remaining_budget_not_the_full_one(monkeypatch):
    """Firecrawl's seconds come out of the same budget."""
    monkeypatch.setattr(intel, "FIRECRAWL_KEY", "")
    monkeypatch.setattr(intel.requests, "get",
                        lambda *a, **k: FakeHTTPResponse(200, "<html></html>"))
    calls = []
    _fake_render(monkeypatch, record=calls)

    intel.fetch_site_content("example-dental.com")

    assert calls, "the Playwright tier was never reached"
    _, budget = calls[0]
    assert budget is not None, "render was called with no budget cap"
    assert budget <= intel.SCRAPE_BUDGET_SECONDS


# --------------------------------------------------------------------------
# Concurrency — Chromium is ~300MB and api.py runs builds on a thread pool
# --------------------------------------------------------------------------

def test_concurrent_renders_are_capped(driver, monkeypatch):
    """Unbounded, N simultaneous prospects mean N browsers and an OOM that takes
    down builds which never touched a browser."""
    d = driver(Driver())
    monkeypatch.setattr(intel, "_PLAYWRIGHT_SLOTS", threading.BoundedSemaphore(1))
    intel._PLAYWRIGHT_SLOTS.acquire()          # the only slot is already taken

    # budget_seconds keeps the queue wait at ~1s instead of the default 14.
    text, html, final_url = intel._render_with_playwright(URL, budget_seconds=7)

    assert (text, html, final_url) == ("", "", URL)
    assert d.events == [], "started a browser past the concurrency cap"


def test_queue_time_is_charged_to_the_render_budget(driver, monkeypatch):
    """Waiting for a slot spends the build's seconds too.

    With the deadline computed after the acquire, a 14s queue plus a 20s render
    added up to 34s inside a 20s ceiling — the tier's own cap silently stopped
    meaning anything under load.
    """
    d = driver(Driver())
    monkeypatch.setattr(intel, "_PLAYWRIGHT_SLOTS", threading.BoundedSemaphore(1))
    intel._PLAYWRIGHT_SLOTS.acquire()

    start = time.monotonic()
    intel._render_with_playwright(URL, budget_seconds=8)
    waited = time.monotonic() - start

    assert waited <= 8, f"waited {waited:.1f}s for a slot inside an 8s budget"


def test_the_slot_is_returned_after_a_successful_render(driver, monkeypatch):
    d = driver(Driver())
    monkeypatch.setattr(intel, "_PLAYWRIGHT_SLOTS", threading.BoundedSemaphore(1))

    intel._render_with_playwright(URL)

    assert intel._PLAYWRIGHT_SLOTS.acquire(timeout=0.1), "slot leaked on success"


def test_the_slot_is_returned_after_a_failed_render(driver, monkeypatch):
    """A leaked slot is permanent: every later build in the process silently
    drops to the fetch tier, with nothing in the logs to explain it."""
    d = driver(Driver(launch_raises=RuntimeError("Executable doesn't exist")))
    monkeypatch.setattr(intel, "_PLAYWRIGHT_SLOTS", threading.BoundedSemaphore(1))

    intel._render_with_playwright(URL)

    assert intel._PLAYWRIGHT_SLOTS.acquire(timeout=0.1), "slot leaked on failure"


def test_timeouts_are_bounded_against_the_build_deadline():
    """leadscraper aborts the engine call at 135s and generation needs ~82-95s.

    Firecrawl 25s + Playwright 20s + fetch 15s only fits because
    SCRAPE_BUDGET_SECONDS caps the sum; without that the tiers add up to 60s and
    the build times out — the exact failure a fallback chain exists to prevent.
    """
    assert intel.PLAYWRIGHT_TIMEOUT_MS <= 25_000
    assert intel.SCRAPE_BUDGET_SECONDS <= 40
    assert intel.SCRAPE_BUDGET_SECONDS + 95 <= 135


def test_adding_a_browser_did_not_raise_the_worst_case():
    """The old chain's worst path was Firecrawl 25s + fetch 15s = 40s.

    The browser has to fit INSIDE that, not extend it: the budget is what is
    left after Firecrawl, and the fetch below still gets its 5s floor. If
    someone raises SCRAPE_BUDGET_SECONDS to 'give Playwright room', this is the
    test that should stop them — the room has to come from Firecrawl's share.
    """
    firecrawl_worst = 25
    fetch_floor = 5
    worst = firecrawl_worst + (intel.SCRAPE_BUDGET_SECONDS - firecrawl_worst) + fetch_floor
    assert worst <= 40, f"worst-case scrape is now {worst}s, was 40s before Playwright"
