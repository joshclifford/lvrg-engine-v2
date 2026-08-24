"""generate_multi_page_site orchestrates generate_page sequentially — never
parallel — resolves design ONCE, wipes the folder before writing, and fails
fast so a partial multi-page site with dead nav links never gets deployed."""

import os

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

    assert calls == ["index", "about", "contact"]
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

    # Fails fast — the third page is never attempted once the second fails.
    assert calls == ["index", "about"]


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
        assert 'id="lvrg-chat-btn"' in html
        assert '<base href="/preview/acme-com/">' in html
