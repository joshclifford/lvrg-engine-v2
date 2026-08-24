"""plan_pages must be deterministic and mockable (POD01-53's own acceptance
criteria) — no Claude call, pure function of intel."""

import pages


def _thin_intel():
    return {
        "business_name": "Joe's Food Truck",
        "description": "Tacos.",
        "services": [],
        "press_mentions": [],
    }


def _rich_intel():
    return {
        "business_name": "Acme Dental",
        "description": "Acme Dental has served North Park families for over twenty "
                        "years with gentle, modern care and same-day appointments.",
        "services": ["Cleanings", "Whitening", "Invisalign", "Emergency care"],
        "press_mentions": [{"source": "SD Union-Tribune", "title": "Best of North Park"}],
    }


def test_thin_business_degrades_to_home_and_contact_only():
    plan = pages.plan_pages(_thin_intel())
    assert [p["slug"] for p in plan] == ["index", "contact"]


def test_rich_business_gets_about_and_services():
    plan = pages.plan_pages(_rich_intel(), max_pages=4)
    slugs = [p["slug"] for p in plan]
    assert slugs == ["index", "about", "services", "contact"]


def test_capped_at_max_pages_without_dropping_contact():
    # Rich enough for About + Services, but the cap only has room for one of
    # them alongside Home + Contact — Contact must never be the one dropped.
    plan = pages.plan_pages(_rich_intel(), max_pages=3)
    assert plan[0]["slug"] == "index"
    assert plan[-1]["slug"] == "contact"
    assert len(plan) == 3


def test_home_is_always_first_and_index_html():
    for intel in (_thin_intel(), _rich_intel()):
        plan = pages.plan_pages(intel)
        assert plan[0]["slug"] == "index"
        assert plan[0]["filename"] == "index.html"


def test_no_filename_collisions():
    plan = pages.plan_pages(_rich_intel())
    filenames = [p["filename"] for p in plan]
    assert len(filenames) == len(set(filenames))


def test_services_page_only_with_at_least_two_services():
    intel = _thin_intel()
    intel["services"] = ["Tacos"]  # one service — not enough for its own page
    plan = pages.plan_pages(intel)
    assert "services" not in [p["slug"] for p in plan]


def test_about_page_only_with_substantial_description_or_press():
    intel = _thin_intel()
    intel["press_mentions"] = [{"source": "Local Blog", "title": "Great tacos"}]
    plan = pages.plan_pages(intel)
    assert "about" in [p["slug"] for p in plan]


def test_returns_fresh_dicts_every_call():
    intel = _rich_intel()
    plan_a = pages.plan_pages(intel)
    plan_b = pages.plan_pages(intel)
    for a, b in zip(plan_a, plan_b):
        assert a is not b
        assert a == b


def test_max_pages_floor_is_two():
    plan = pages.plan_pages(_rich_intel(), max_pages=0)
    assert [p["slug"] for p in plan] == ["index", "contact"]
