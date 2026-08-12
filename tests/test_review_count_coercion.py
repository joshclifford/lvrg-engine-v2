"""A review count that is not an int must not crash the build.

`intel["review_count"]` has two sources: leadscraper (a real int) and
extract_intel_with_claude, whose output is model-generated JSON. The zero guard
added on 2026-08-12 compared with `> 0`, which raises TypeError on a str in
Python 3 — turning a cosmetic prompt bug into a failed build on input the old
`is not None` check rendered fine.

_as_count normalises first. Anything unusable becomes None and routes to the
no-count branch, which tells the model not to state a review count at all.
"""

import pytest

from generator import _as_count


@pytest.mark.parametrize("raw,expected", [
    (12, 12),
    (0, 0),
    ("12", 12),
    ("  12  ", 12),
    ("1,204", 1204),
    (12.0, 12),
])
def test_usable_counts_become_ints(raw, expected):
    assert _as_count(raw) == expected


@pytest.mark.parametrize("raw", [
    None,
    "",
    "   ",
    "no reviews",
    "12 reviews",
    "N/A",
    [],
    {},
    True,          # bool is an int subclass — never a review count
    False,
])
def test_unusable_values_become_none(raw):
    assert _as_count(raw) is None


def test_string_zero_still_takes_the_no_count_branch():
    """The whole point: "0" must behave exactly like 0, not crash and not boast."""
    assert _as_count("0") == 0


def test_comparison_that_used_to_raise_is_now_safe():
    for raw in ["12", "0", "no reviews", None, 12]:
        count = _as_count(raw)
        # This is the expression in the prompt block. It must never raise.
        assert (count is not None and count > 0) in (True, False)
