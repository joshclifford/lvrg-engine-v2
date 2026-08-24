"""v2 only — the quotes branch must stay deleted.

`intel["reviews"]` was set to [] by scrape_site and populated by nothing:
_merge_known has no `reviews` key and leadscraper never sends one. So the
`if reviews:` branch in generate_site was unreachable — and it still carried
"REAL CUSTOMER REVIEWS (use these verbatim as testimonials)".

That instruction was written for the Yelp scrape, whose regex
`"text":"([^"]{40,200})"` matched any JSON string field named `text` anywhere on
the page — ad copy, category blurbs, other businesses' content — and handed the
results to the model as real customer quotes. Feeding a real business's branded
page arbitrary strangers' sentences as its own testimonials is worse than
inventing one.

The branch is gone. These tests exist so it cannot come back by accident: it
survived a full release cycle as dead code, and one assignment to `intel`
would have re-armed it.
"""

import inspect

import generator
import intel


def _code_of(fn) -> str:
    return "\n".join(
        line for line in inspect.getsource(fn).splitlines()
        if not line.lstrip().startswith("#")
    )


def test_generator_has_no_reviews_branch():
    # Moved out of generate_site into _build_reviews_block (multi-page
    # generation reuses this same builder per page) — the guard follows.
    code = _code_of(generator._build_reviews_block)
    assert 'intel.get("reviews"' not in code
    assert "if reviews:" not in code


def test_no_verbatim_quote_instruction_survives():
    code = _code_of(generator._build_reviews_block).upper()
    assert "REAL CUSTOMER REVIEWS" not in code
    assert "VERBATIM AS TESTIMONIALS" not in code


def test_scrape_site_does_not_publish_a_reviews_key():
    """If the key is absent, a future `if reviews:` fails loudly at review time
    instead of silently returning [] forever."""
    assert '"reviews"' not in _code_of(intel.scrape_site)


def test_yelp_is_gone_entirely():
    """Yelp 403s datacenter IPs, so the whole path returned '0 photos, 0 review
    snippets, rating None' down the SUCCESS path with no exception raised.

    Asserts on callable names, keys and URLs — not on the word. The removal is
    explained in comments in both modules, and those explanations name Yelp on
    purpose; banning the word would delete the reason anyone would know why.
    """
    for module in (intel, generator):
        names = [n for n in dir(module) if "yelp" in n.lower()]
        assert not names, f"{module.__name__} still exposes {names}"

    for module in (intel, generator):
        code = "\n".join(
            line for line in inspect.getsource(module).splitlines()
            if not line.lstrip().startswith("#")
        ).lower()
        assert "yelp.com" not in code
        assert "yelpcdn" not in code
        assert "yelp_" not in code          # yelp_photos, yelp_rating, yelp_reviews


def test_press_search_is_budget_guarded():
    """Press is the one optional stage. Without the guard a slow scrape pushes
    the build past the caller's 135s ceiling."""
    assert intel.INTEL_BUDGET_SECONDS <= 45
    assert "INTEL_BUDGET_SECONDS" in _code_of(intel.scrape_site)
