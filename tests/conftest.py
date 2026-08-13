"""Keep the unit suite off the network and out of a browser.

`fetch_site_content` now has a Playwright tier between Firecrawl and the direct
fetch. Tests that monkeypatch `requests` do not know about it, so without this
they launch a real Chromium and navigate to whatever fake domain the test used
— ~0.5s each, and a hard failure on any CI box without the browser binary
installed. The tier is off by default here and opted into explicitly by
test_playwright_render.py, which fakes the driver rather than running one.
"""

import pytest

import intel


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch, request):
    if "playwright_allowed" in request.keywords:
        return
    monkeypatch.setattr(intel, "PLAYWRIGHT_ENABLED", False)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "playwright_allowed: test drives the Playwright tier with a fake driver",
    )
