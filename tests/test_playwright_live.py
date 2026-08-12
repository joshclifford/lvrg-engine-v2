"""Live-browser tests for the scroll script. Opt-in.

Everything else in the suite fakes the driver, which is right for control flow
but cannot prove the thing that actually bit: whether the injected JavaScript
settles. `page.evaluate` has no timeout and ignores `set_default_timeout`, so a
promise that never resolves is not a slow render — it is a hung build. The first
version of `_SCROLL_JS` did exactly that on a document with no body, for over
three minutes, against a 140s engine deadline.

Structural assertions in test_playwright_render.py pin the SHAPE of the fix.
These run it.

    RUN_PLAYWRIGHT_LIVE=1 pytest tests/test_playwright_live.py

Kept out of the default run because it needs the browser binary installed, and a
CI box without one should report "skipped", not "failed".
"""

import os
import time

import pytest

import intel

pytestmark = [
    pytest.mark.playwright_allowed,
    pytest.mark.skipif(
        not os.environ.get("RUN_PLAYWRIGHT_LIVE"),
        reason="live browser test — set RUN_PLAYWRIGHT_LIVE=1 to run",
    ),
]

# Every one of these settled in under 2.6s with the fix. The old script hung
# indefinitely on `body deleted`.
PAGES = {
    "normal": "data:text/html,<body><p>hi</p></body>",
    "tall": "data:text/html,<body>" + "<p>x</p>" * 4000 + "</body>",
    "empty": "data:text/html,",
    "xml": "data:application/xml,<rss><channel/></rss>",
    "body deleted": (
        "data:text/html,<body><script>"
        "document.documentElement.removeChild(document.body)</script>"
    ),
    "infinite scroll": (
        "data:text/html,<body style='height:99999px'><script>"
        "setInterval(()=>{document.body.style.height="
        "(parseInt(document.body.style.height)+50000)+'px'},50)"
        "</script></body>"
    ),
}

# Generous against the ~2.5s worst case observed, tight against the 30s+ that
# a non-settling promise costs.
SETTLE_CEILING_SECONDS = 8


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = browser.new_page()
    yield p
    browser.close()
    pw.stop()


@pytest.mark.parametrize("name", list(PAGES))
def test_scroll_settles_quickly(page, name):
    page.goto(PAGES[name])

    started = time.monotonic()
    page.evaluate(intel._SCROLL_JS)
    elapsed = time.monotonic() - started

    assert elapsed < SETTLE_CEILING_SECONDS, (
        f"{name!r} took {elapsed:.1f}s to settle — page.evaluate has no timeout, "
        f"so this is a hung build, not a slow one"
    )


@pytest.mark.parametrize("name", list(PAGES))
def test_lazy_promotion_never_throws(page, name):
    """It runs inside the same try as the scroll; a throw costs the photos."""
    page.goto(PAGES[name])

    assert isinstance(page.evaluate(intel._PROMOTE_LAZY_JS), int)


def test_lazy_promotion_replaces_a_placeholder_src(page):
    page.goto(
        "data:text/html,<body>"
        "<img src='data:image/gif;base64,R0lGOD' data-src='/photos/hero.jpg'>"
        "<img src='https://example.com/real.jpg' data-src='/photos/nope.jpg'>"
        "</body>"
    )

    promoted = page.evaluate(intel._PROMOTE_LAZY_JS)
    srcs = page.evaluate(
        "() => Array.from(document.querySelectorAll('img'))"
        ".map(i => i.getAttribute('src'))"
    )

    assert promoted == 1, "promoted the wrong number of images"
    assert srcs[0].endswith("/photos/hero.jpg"), "placeholder was not replaced"
    assert srcs[1] == "https://example.com/real.jpg", "overwrote a real src"
