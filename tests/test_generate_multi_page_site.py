"""generate_multi_page_site orchestrates generate_page CONCURRENTLY (24 Aug
2026 — was strictly sequential; see PAGE_GENERATION_CONCURRENCY in
generator.py for why), resolves design ONCE, wipes the folder before
writing, and still fails fast in the sense that matters: if any page's call
raises, nothing gets returned and nothing gets deployed — a partial
multi-page site with dead nav links never ships. What concurrency gives up
is stopping EARLY: every planned page is already in flight by the time one
of them fails, so a failure no longer skips the pages behind it."""

import os
import threading
import time

import pytest

import generator


def _pages_plan():
    return [
        {"slug": "index", "filename": "index.html", "title": "Home", "brief": "b"},
        {"slug": "about", "filename": "about.html", "title": "About", "brief": "b"},
        {"slug": "contact", "filename": "contact.html", "title": "Contact", "brief": "b"},
    ]


def _intel():
    return {"business_name": "Acme", "business_type": "other"}


def test_calls_generate_page_once_per_planned_page(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    calls = []

    def fake_generate_page(intel, design, page, nav, notes="", r6=None):
        calls.append(page["slug"])
        return f"<html><body>{page['slug']}</body></html>"

    monkeypatch.setattr(generator, "generate_page", fake_generate_page)

    result = generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())

    # Order is no longer guaranteed — pages are dispatched concurrently, so
    # whichever thread's `calls.append` runs first depends on scheduling.
    assert set(calls) == {"index", "about", "contact"}
    assert set(result.keys()) == {"index", "about", "contact"}


def test_same_design_object_passed_to_every_page(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    seen_designs = []

    def fake_generate_page(intel, design, page, nav, notes="", r6=None):
        seen_designs.append(design)
        return "<html><body>x</body></html>"

    monkeypatch.setattr(generator, "generate_page", fake_generate_page)
    generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())

    assert all(d is seen_designs[0] for d in seen_designs), (
        "design must be resolved once and reused, not re-derived per page"
    )


def test_files_written_with_planned_filenames(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    monkeypatch.setattr(
        generator, "generate_page",
        lambda intel, design, page, nav, notes="", r6=None: f"<html><body>{page['slug']}</body></html>",
    )

    site_paths = generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())

    site_dir = os.path.join(str(tmp_path), "acme-com")
    assert set(os.listdir(site_dir)) == {"index.html", "about.html", "contact.html"}
    assert site_paths["about"] == os.path.join(site_dir, "about.html")


def test_stale_page_from_a_prior_build_is_removed(monkeypatch, tmp_path):
    # A retry that no longer plans a Services page must not leave a stale
    # services.html sitting in the folder for deploy_site to push alongside
    # the new set — that would ship a page nothing links to and nothing knows
    # is stale.
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    site_dir = os.path.join(str(tmp_path), "acme-com")
    os.makedirs(site_dir)
    with open(os.path.join(site_dir, "services.html"), "w") as f:
        f.write("stale")

    monkeypatch.setattr(
        generator, "generate_page",
        lambda intel, design, page, nav, notes="", r6=None: f"<html><body>{page['slug']}</body></html>",
    )
    generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())

    assert "services.html" not in os.listdir(site_dir)


def test_one_failing_page_raises_and_nothing_deployed(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    calls = []

    def failing_generate_page(intel, design, page, nav, notes="", r6=None):
        calls.append(page["slug"])
        if page["slug"] == "about":
            raise RuntimeError("Claude call failed")
        return f"<html><body>{page['slug']}</body></html>"

    monkeypatch.setattr(generator, "generate_page", failing_generate_page)

    with pytest.raises(RuntimeError, match="Claude call failed"):
        generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())

    # Every page is dispatched concurrently, so unlike the old sequential
    # version, "about" failing does NOT stop "contact" from being attempted —
    # it was already in flight. What's preserved is the part that matters:
    # the exception still propagates and nothing gets written/deployed.
    assert set(calls) == {"index", "about", "contact"}
    assert os.listdir(os.path.join(str(tmp_path), "acme-com")) == [], (
        "a page failed — no file for ANY page should have been written"
    )


def test_chat_widget_and_base_href_injected_into_every_page(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    monkeypatch.setattr(
        generator, "generate_page",
        lambda intel, design, page, nav, notes="", r6=None:
            "<!DOCTYPE html><html><head></head><body>x</body></html>",
    )

    site_paths = generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())

    for path in site_paths.values():
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        assert 'leadconnectorhq.com' in html  # GHL widget (6235ccd), not the retired self-hosted one
        assert '<base href="/preview/acme-com/">' in html


def test_pages_actually_overlap_in_wall_time(monkeypatch, tmp_path):
    """The whole point of this change: 3 pages that each take 0.3s must NOT
    take ~0.9s total. If this ever regresses to sequential, this is the test
    that catches it — the others only check the OUTPUT, not the timing."""
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))

    def slow_generate_page(intel, design, page, nav, notes="", r6=None):
        time.sleep(0.3)
        return f"<html><body>{page['slug']}</body></html>"

    monkeypatch.setattr(generator, "generate_page", slow_generate_page)

    started = time.monotonic()
    generator.generate_multi_page_site(_intel(), "acme-com", _pages_plan())
    elapsed = time.monotonic() - started

    # 3 pages sequentially would be ~0.9s; concurrently, ~0.3s. 0.7s is a
    # generous cutoff that still fails loudly if this regresses to serial.
    assert elapsed < 0.7, f"took {elapsed:.2f}s — looks sequential, not concurrent"


def test_concurrency_never_exceeds_the_configured_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))
    monkeypatch.setattr(generator, "PAGE_GENERATION_CONCURRENCY", 2)

    in_flight = 0
    peak_in_flight = 0
    lock = threading.Lock()

    def tracked_generate_page(intel, design, page, nav, notes="", r6=None):
        nonlocal in_flight, peak_in_flight
        with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        time.sleep(0.15)  # hold the slot long enough for others to queue up
        with lock:
            in_flight -= 1
        return f"<html><body>{page['slug']}</body></html>"

    monkeypatch.setattr(generator, "generate_page", tracked_generate_page)

    # 4 pages against a cap of 2 — peak concurrency must never exceed 2.
    plan = _pages_plan() + [
        {"slug": "services", "filename": "services.html", "title": "Services", "brief": "b"},
    ]
    generator.generate_multi_page_site(_intel(), "acme-com", plan)

    assert peak_in_flight == 2, f"peak concurrent calls was {peak_in_flight}, cap was 2"


def test_a_rate_limit_error_is_retried_by_the_client_not_by_hand(monkeypatch):
    """PAGE_GENERATION_MAX_RETRIES is passed to the SDK client, not re-implemented
    here — this pins down that generate_page asks for MORE retries than the
    single-page path's default, since concurrent calls raise 429 odds."""
    seen_kwargs = []
    monkeypatch.setattr(
        generator, "_get_client",
        lambda **kwargs: seen_kwargs.append(kwargs) or _FakeAnthropicClient(),
    )

    design = generator._get_design_personality("other")
    nav = _pages_plan()
    intel = _intel() | {"domain": "acme.com", "description": "", "services": [], "location": "San Diego, CA"}
    generator.generate_page(intel, design, nav[0], nav)

    assert seen_kwargs[0] == {"max_retries": generator.PAGE_GENERATION_MAX_RETRIES}
    assert generator.PAGE_GENERATION_MAX_RETRIES > 2, (
        "must exceed the anthropic SDK's own default of 2 to matter for a "
        "concurrent burst"
    )


class _FakeAnthropicClient:
    class _Stream:
        def __enter__(self):
            from types import SimpleNamespace
            return SimpleNamespace(
                get_final_message=lambda: SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(text="<!DOCTYPE html><html><body>x</body></html>")],
                )
            )

        def __exit__(self, *a):
            return False

    class _Messages:
        def stream(self, **kwargs):
            return _FakeAnthropicClient._Stream()

    messages = _Messages()
