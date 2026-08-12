"""_merge_known — caller-supplied facts overlaid on scraped intel.

The bug this file exists for: `known={"rating": 4.5}` with no review count wrote
`review_count=None`, which the generator interpolated into its prompt as the
literal string — "rated 4.5★ from None reviews" — on a page carrying the
prospect's own branding. Both the TypeScript caller and this function's own
docstring claimed it could not happen, because the guard was on `rating` rather
than per field.
"""

from api import _merge_known


def test_rating_without_a_count_does_not_blank_an_existing_count():
    """The documented contract: "a caller sending partial data can't blank out
    something the scrape did find". Writing review_count unconditionally broke
    it — a `known` carrying a rating and no count wiped a count we already had.

    (Asserting `is None` against a None start would pass either way, which is
    why this starts from a populated value.)"""
    intel = {"rating": None, "review_count": 212}
    used = _merge_known(intel, {"rating": 4.5})

    assert intel["rating"] == 4.5
    assert intel["review_count"] == 212
    assert "rating" in used


def test_rating_and_count_together_both_land():
    intel = {"rating": None, "review_count": None}
    _merge_known(intel, {"rating": 4.5, "review_count": 212})

    assert intel["rating"] == 4.5
    assert intel["review_count"] == 212


def test_rating_zero_survives():
    """Checked against None, not truthiness — a genuine 0 is real data."""
    intel = {"rating": None, "review_count": None}
    _merge_known(intel, {"rating": 0, "review_count": 0})

    assert intel["rating"] == 0
    assert intel["review_count"] == 0


def test_partial_payload_cannot_blank_scraped_values():
    intel = {"phone": "555-0100", "email": "hi@example.com"}
    _merge_known(intel, {"phone": "", "email": None})

    assert intel["phone"] == "555-0100"
    assert intel["email"] == "hi@example.com"


def test_address_maps_to_location_and_is_trimmed():
    """The app's column is `address`; the engine calls it `location`."""
    intel = {}
    used = _merge_known(intel, {"address": "  12 Main St, Boston MA  "})

    assert intel["location"] == "12 Main St, Boston MA"
    assert "location" in used


def test_socials_only_included_when_present():
    intel = {}
    _merge_known(intel, {"facebook_url": "https://fb.com/x", "instagram_url": ""})

    assert intel["socials"] == {"facebook_url": "https://fb.com/x"}


def test_empty_known_is_a_no_op():
    intel = {"phone": "555-0100"}
    assert _merge_known(intel, {}) == []
    assert intel == {"phone": "555-0100"}
