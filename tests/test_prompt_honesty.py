"""Tripwires on the generator prompt and the intel extraction prompt.

Not unit tests — guards on text that has caused real harm on published pages.
Each one marks a line that was live in production and should not come back
without a deliberate decision.

Every assertion runs against the code with COMMENT LINES STRIPPED. The fixes
leave comments explaining what was removed and why, and those comments quote the
very strings being guarded against — asserting on raw source would fail on the
explanation rather than the defect. (Learned the hard way: the first version of
`test_grade_does_not_special_case_one_city` failed on its own fix's comment.)

This file is identical in lvrg-engine and lvrg-engine-v2 by design. Port it, do
not fork it — the two engines' prompts differ in wording, so every assertion
here is written against structure that holds in both.
"""

import inspect
import re

import generator
import intel


def _code_of(fn) -> str:
    """Source of `fn` with comment-only lines removed."""
    return "\n".join(
        line for line in inspect.getsource(fn).splitlines()
        if not line.lstrip().startswith("#")
    )


# ── Social proof ──────────────────────────────────────────────────────────

def test_no_invented_testimonials():
    """The prompt used to ask for 2-3 pull quotes and say to 'write them fresh'.
    No real review text is ever supplied, so no quote can be legitimate.

    Moved out of generate_site into _build_reviews_block (multi-page
    generation reuses this same builder per page) — the guard follows."""
    code = _code_of(generator._build_reviews_block).lower()
    assert "write them fresh" not in code
    assert "use these verbatim as testimonials" not in code


def test_review_count_is_guarded_independently_of_rating():
    """A rating and a count arrive separately — Apify returns listings with one
    and not the other. Branching on the rating alone interpolated the missing
    count as the literal 'None' and published 'from None reviews'."""
    code = _code_of(generator._build_reviews_block)
    if "{review_count}" in code:
        assert "review_count is not None" in code, (
            "review_count is interpolated without ever being checked — this is "
            "how 'from None reviews' reached a published page"
        )


def test_rating_without_a_count_has_its_own_branch():
    code = _code_of(generator._build_reviews_block).lower()
    assert "do not state one" in code


# ── Location ──────────────────────────────────────────────────────────────

def test_extraction_prompt_is_not_pinned_to_one_city():
    """The prompt asked for a 'Specific San Diego neighborhood' for every
    business on earth, which is half of how a fabricated location shipped."""
    assert not re.search(
        r"San Diego neighborhood", _code_of(intel.extract_intel_with_claude), re.I
    )


def test_location_is_never_defaulted_to_a_city():
    """The other half: location defaulted to "San Diego, CA", and the generator
    is told to reference it "naturally in copy" — so an unparsed address became
    a real business publicly described as being in a city it is not in."""
    code = _code_of(intel.scrape_site)
    assert 'extracted.get("location", "San Diego' not in code
    assert 'extracted.get("location", "")' in code


def test_grade_does_not_special_case_one_city():
    """`location != "San Diego, CA"` stood in for "not extracted". With the
    default now empty it would only ever penalise genuine San Diego businesses."""
    assert '!= "San Diego' not in _code_of(intel.grade_site)
