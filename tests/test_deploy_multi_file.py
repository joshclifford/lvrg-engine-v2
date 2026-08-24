"""deploy_site pushes every *.html file in site_dir as ONE atomic commit
(one tree with N entries), not N separate commits — a partial push would let
a nav bar go live linking to pages that never made it to GitHub Pages.

A single-file folder (today's only real caller shape, one index.html) must
produce the exact same _api call sequence as before this function
generalized to N files — that's the regression guarantee for the existing
single-page path.
"""

import os

import pytest

import deploy


@pytest.fixture(autouse=True)
def _no_verify_wait(monkeypatch):
    # _wait_until_live polls over real wall-clock time and hits the network
    # (_is_live). Neither belongs in a unit test; deploy_site's return value
    # doesn't depend on which branch this takes.
    monkeypatch.setattr(deploy, "_wait_until_live", lambda url: True)


def _fake_api(calls):
    """Records every _api call and returns just enough shape for deploy_site
    to keep going: a sha for blobs/trees/commits, a nested ref/commit shape
    for the two GET calls."""

    def _api(method, path, body=None):
        calls.append((method, path, body))
        if path == "git/ref/heads/main":
            return {"object": {"sha": "head-sha"}}
        if path.startswith("git/commits/") and method == "GET":
            return {"tree": {"sha": "base-tree-sha"}}
        if path == "git/blobs":
            return {"sha": f"blob-sha-{len([c for c in calls if c[1] == 'git/blobs'])}"}
        if path == "git/trees":
            return {"sha": "new-tree-sha"}
        if path == "git/commits":
            return {"sha": "new-commit-sha"}
        if path == "git/refs/heads/main":
            return {}
        raise AssertionError(f"unexpected _api call: {method} {path}")

    return _api


def test_single_file_folder_matches_pre_multi_page_call_sequence(tmp_path, monkeypatch):
    site_dir = tmp_path / "acme-com"
    site_dir.mkdir()
    (site_dir / "index.html").write_bytes(b"<html>home</html>")

    calls = []
    monkeypatch.setattr(deploy, "_api", _fake_api(calls))

    url = deploy.deploy_site("acme-com", str(site_dir))

    assert url == "https://joshclifford.github.io/lvrg-previews/acme-com/index.html"

    blob_calls = [c for c in calls if c[1] == "git/blobs"]
    tree_calls = [c for c in calls if c[1] == "git/trees"]
    commit_calls = [c for c in calls if c[1] == "git/commits" and c[0] == "POST"]
    ref_updates = [c for c in calls if c[1] == "git/refs/heads/main"]

    assert len(blob_calls) == 1
    assert len(tree_calls) == 1
    assert tree_calls[0][2]["tree"] == [
        {"path": "acme-com/index.html", "mode": "100644", "type": "blob", "sha": "blob-sha-1"}
    ]
    assert len(commit_calls) == 1
    assert len(ref_updates) == 1


def test_multi_page_folder_pushes_one_commit_with_n_tree_entries(tmp_path, monkeypatch):
    site_dir = tmp_path / "acme-com"
    site_dir.mkdir()
    (site_dir / "index.html").write_bytes(b"<html>home</html>")
    (site_dir / "about.html").write_bytes(b"<html>about</html>")
    (site_dir / "services.html").write_bytes(b"<html>services</html>")
    (site_dir / "contact.html").write_bytes(b"<html>contact</html>")

    calls = []
    monkeypatch.setattr(deploy, "_api", _fake_api(calls))

    url = deploy.deploy_site("acme-com", str(site_dir))

    # Homepage URL, still — the one thing smart_site_url stores downstream.
    assert url == "https://joshclifford.github.io/lvrg-previews/acme-com/index.html"

    blob_calls = [c for c in calls if c[1] == "git/blobs"]
    tree_calls = [c for c in calls if c[1] == "git/trees"]
    commit_calls = [c for c in calls if c[1] == "git/commits" and c[0] == "POST"]
    ref_updates = [c for c in calls if c[1] == "git/refs/heads/main"]

    assert len(blob_calls) == 4
    assert len(tree_calls) == 1, "must be ONE tree with 4 entries, not 4 separate pushes"
    paths = {entry["path"] for entry in tree_calls[0][2]["tree"]}
    assert paths == {
        "acme-com/index.html",
        "acme-com/about.html",
        "acme-com/services.html",
        "acme-com/contact.html",
    }
    assert len(commit_calls) == 1, "must be ONE atomic commit, not one per file"
    assert len(ref_updates) == 1


def test_non_html_files_are_ignored(tmp_path, monkeypatch):
    site_dir = tmp_path / "acme-com"
    site_dir.mkdir()
    (site_dir / "index.html").write_bytes(b"<html>home</html>")
    (site_dir / "notes.txt").write_bytes(b"not a page")

    calls = []
    monkeypatch.setattr(deploy, "_api", _fake_api(calls))

    deploy.deploy_site("acme-com", str(site_dir))

    tree_calls = [c for c in calls if c[1] == "git/trees"]
    paths = {entry["path"] for entry in tree_calls[0][2]["tree"]}
    assert paths == {"acme-com/index.html"}


def test_empty_folder_raises_instead_of_deploying_nothing(tmp_path, monkeypatch):
    site_dir = tmp_path / "acme-com"
    site_dir.mkdir()

    monkeypatch.setattr(deploy, "_api", _fake_api([]))

    with pytest.raises(RuntimeError, match="No .html files"):
        deploy.deploy_site("acme-com", str(site_dir))
