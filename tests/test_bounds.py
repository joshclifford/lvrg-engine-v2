"""Budget and prompt-size guards.

These assert that the bounds exist and stay sane, not that a specific number is
correct. Both were removed-or-absent in production: the extraction prompt had no
ceiling at all after the 4,000-char scrape cap was dropped, and every kept photo
paid a blocking HEAD inside a 135s build budget with no slack.
"""

import inspect

import intel
import generator


def test_extraction_input_is_bounded():
    assert 10_000 <= intel.EXTRACT_MAX_CHARS <= 200_000


def test_extraction_call_site_applies_the_cap():
    src = inspect.getsource(intel.scrape_site)
    assert "raw_text[:EXTRACT_MAX_CHARS]" in src, "cap defined but not applied"


def test_generator_slice_is_still_capped():
    """The generator's sample was always capped; the fix must not remove it."""
    assert "raw_text[:6000]" in inspect.getsource(intel.scrape_site)


def test_full_size_upgrade_is_limited_to_the_hero():
    assert intel._UPGRADE_TOP_N <= 2
    assert intel._UPGRADE_HEAD_TIMEOUT <= 2


def test_worst_case_upgrade_cost_fits_the_budget():
    """<= 4s of blocking HEADs, against a 135s ceiling already ~82s spent on
    generation and 25s on the Firecrawl scrape."""
    assert intel._UPGRADE_TOP_N * intel._UPGRADE_HEAD_TIMEOUT <= 4


def test_site_token_ceiling_is_set():
    assert generator.SITE_MAX_TOKENS >= 16_000
