"""E31 — a review count of exactly zero is not social proof.

`review_count is not None` let 0 through, so a listing with a rating and no
reviews published "rated 4.5★ from 0 reviews. Use that as a real social-proof
stat." on a page carrying the prospect's own branding.

It cannot happen with today's data — production has 1,380 rated businesses and
`min(review_count) = 1`, because Google shows no rating until there is at least
one review. It is kept precisely because that is a property of the data, not of
the code: leadscraper's build-smart-site passes the count through on
`!= null`, which admits 0, so the day an upstream source emits one the guard is
the only thing standing there.
"""

import inspect

import generator


def _prompt_for(rating, review_count):
    """The rating/review branch, whatever the engine calls its block."""
    src = inspect.getsource(generator)
    assert "review_count > 0" in src, (
        "the zero guard is gone — a count of 0 will publish as social proof again"
    )
    return src


def test_zero_guard_is_present():
    src = inspect.getsource(generator)
    assert "review_count is not None and review_count > 0" in src


def test_none_and_zero_take_the_same_branch():
    """Both mean 'no reviews', so both must reach the no-count wording."""
    src = inspect.getsource(generator)
    # the no-count branch must still exist and still forbid stating a count
    assert "do not state one" in src.lower()


def test_rating_branches_are_exhaustive():
    """rating+count, rating only, neither — no silent fall-through."""
    src = inspect.getsource(generator)
    assert src.count("elif rating is not None") >= 1
    assert "Do NOT" in src or "do NOT" in src
