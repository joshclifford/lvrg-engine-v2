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


# ── the prompt, not just the code ───────────────────────────────────────────
# The tests above pin the deleted quotes branch in _build_reviews_block. They do
# not look at what the PROMPT asks for, and that is the gap that mattered: while
# the code supplied no quotes, both Smart Site prompts still carried
#
#   "6. TESTIMONIALS — ONLY the review quotes provided above, verbatim."
#
# asking the model to build a section from a source that structurally does not
# exist. output/sites/misadventure/index.html is the result: a Smart Site build
# (its claim bar reads "Claim This Site") carrying three invented customers with
# names and cities. The instruction survived every review of the review-quote
# guard because nothing ever asserted on the rendered prompt.

def _rendered_prompts():
    """Every prompt the three generators actually send, with a fake client."""
    import sys

    sys.path.insert(0, "tests")
    from test_generate_page import _rich_intel, _nav_plan, _mock_client
    import tempfile

    captured = []
    real_client, real_dir = generator._get_client, generator.SITES_DIR
    generator._get_client = lambda **k: _mock_client(captured)
    generator.SITES_DIR = tempfile.mkdtemp()
    try:
        generator.generate_site(_rich_intel(), "acme-com")
        generator.generate_page(
            _rich_intel(), generator._get_design_personality("other"),
            _nav_plan()[0], _nav_plan(),
        )
        generator.generate_offer_lead_magnet_page("get_listed", _rich_intel())
        generator.generate_offer_lead_magnet_page("sponsored_story", _rich_intel())
    finally:
        generator._get_client, generator.SITES_DIR = real_client, real_dir
    # Without this, a capture helper that silently stops working turns all three
    # tests below into empty loops that pass while checking nothing — the same
    # class of dead guard this section exists to prevent.
    assert len(captured) == 4, f"expected 4 prompts, captured {len(captured)}"
    return [str(c) for c in captured]


def test_no_prompt_asks_for_a_testimonials_section():
    """All three generators, not just the two lead magnets. The Smart Site path
    is the one with a confirmed real fabrication behind it."""
    for prompt in _rendered_prompts():
        assert "TESTIMONIALS — ONLY" not in prompt
        assert "review quotes provided above" not in prompt


def test_every_prompt_carries_the_no_review_text_rule():
    """A prompt that drops the rule is a prompt that can invent a reviewer."""
    for prompt in _rendered_prompts():
        assert "YOU WERE GIVEN NO REVIEW TEXT" in prompt


def test_no_prompt_contradicts_itself_about_testimonials():
    """The real defect was not a missing rule, it was two instructions pulling
    opposite ways in one prompt: the reviews block forbidding quoted customers
    while a numbered structure item asked for a Testimonials section. Either
    both are absent or the prompt is telling the model to do the thing it just
    banned."""
    for prompt in _rendered_prompts():
        if "YOU WERE GIVEN NO REVIEW TEXT" in prompt:
            assert "TESTIMONIALS — ONLY" not in prompt
