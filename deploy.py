"""
LVRG Lead Magnet Engine — GitHub Pages Deployer
Uses GitHub Git Data API (blob + tree + commit) to handle files of any size.
No git clone needed. No file size limits.
"""

import os
import base64
import json
import time
import urllib.request
import urllib.error
from config import GITHUB_USER, GITHUB_REPO, PREVIEW_BASE_URL

# GitHub Pages builds asynchronously, so the URL is not live the moment the commit
# ref moves — that gap is what put 404s in front of prospects.
#
# The window is small on purpose, and it is not the only safety net: leadscraper's
# client-side readiness probe covers the first 60s after a build, and since 13 Aug
# the build-outcome callback plus the 5-minute reaper settle the status
# independently. This poll only catches the common case; anything slower is
# reported as UNVERIFIED and left to those.
#
# ONE INTERVAL, NOT FIVE (17 Aug 2026). This was 10-15s, and on a slug GitHub
# Pages has NEVER served — every first build of a sub-page lead since POD01-34 —
# the full window is spent and then fails anyway, because a brand-new path takes
# Pages longer than the window no matter how long the window is. That wasted time
# is what tipped a 158s build to 168s and past leadscraper's 160s abort:
#
#   11:53:47  pushed
#   11:53:54  WARNING: Pages had not served it after 10s — UNVERIFIED
#
# So it now checks once. A new path is UNVERIFIED either way; spending 10s to
# learn that only made the caller give up first.
#
# The old comment here claimed "leadscraper aborts at 135s, and that ceiling
# cannot be raised: it is headroom under Supabase's 150s edge-function lifetime."
# Both halves were false and worth recording: the abort is 160s, and 13 Aug
# measured three executions running a full 200s before shutdown, so the 150s
# figure was stale and the ceiling always had room.
PAGES_VERIFY_TIMEOUT = int(os.environ.get("PAGES_VERIFY_TIMEOUT", "3"))
PAGES_VERIFY_INTERVAL = 3


def _api(method: str, path: str, body: dict = None) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "lvrg-engine")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {method} {path} → {e.code}: {e.read().decode()}")


def _is_live(url: str) -> bool:
    """True once GitHub Pages actually serves the page."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "lvrg-engine")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False  # not propagated yet, or a transient blip


def _wait_until_live(url: str) -> bool:
    """Poll until the preview resolves. False means we ran out of budget."""
    deadline = time.monotonic() + PAGES_VERIFY_TIMEOUT
    while time.monotonic() < deadline:
        if _is_live(url):
            return True
        time.sleep(PAGES_VERIFY_INTERVAL)
    return _is_live(url)


def deploy_site(prospect_id: str, site_dir: str) -> str:
    """Push every *.html file in site_dir to GitHub Pages via Git Data API, in
    one atomic commit. No size limits. Returns the public URL of index.html —
    the homepage — regardless of how many other pages went along with it,
    since that's the one URL smart_site_url stores. A single-page site (one
    index.html, today's only caller shape) produces the exact same blob/tree/
    commit/ref-update sequence as before this function generalized."""

    filenames = sorted(f for f in os.listdir(site_dir) if f.endswith(".html"))
    if not filenames:
        raise RuntimeError(f"No .html files to deploy in {site_dir}")

    print(f"  [deploy] Pushing {prospect_id} ({len(filenames)} page(s)) via Git Data API...")

    # 1. Create one blob per file
    tree_entries = []
    for filename in filenames:
        with open(os.path.join(site_dir, filename), "rb") as f:
            content = f.read()
        blob = _api("POST", "git/blobs", {
            "content": base64.b64encode(content).decode(),
            "encoding": "base64"
        })
        tree_entries.append({
            "path": f"{prospect_id}/{filename}",
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"]
        })

    # 2. Get current HEAD commit and its tree
    ref = _api("GET", "git/ref/heads/main")
    head_sha = ref["object"]["sha"]
    head_commit = _api("GET", f"git/commits/{head_sha}")
    base_tree_sha = head_commit["tree"]["sha"]

    # 3. Create a new tree with all our files, in one commit
    tree = _api("POST", "git/trees", {
        "base_tree": base_tree_sha,
        "tree": tree_entries
    })
    tree_sha = tree["sha"]

    # 4. Create a commit pointing to the new tree
    commit = _api("POST", "git/commits", {
        "message": f"Add preview: {prospect_id}",
        "tree": tree_sha,
        "parents": [head_sha]
    })
    commit_sha = commit["sha"]

    # 5. Update the branch ref to the new commit
    _api("PATCH", "git/refs/heads/main", {
        "sha": commit_sha,
        "force": False
    })

    public_url = f"{PREVIEW_BASE_URL}/{prospect_id}/index.html"

    # Say what actually happened. Returning an unverified URL silently is how a
    # link that 404s ends up in a prospect's inbox looking fine from our side.
    if _wait_until_live(public_url):
        print(f"  [deploy] Live at: {public_url}")
    else:
        print(
            f"  [deploy] WARNING: pushed, but Pages had not served it after "
            f"{PAGES_VERIFY_TIMEOUT}s — returning UNVERIFIED {public_url}"
        )

    return public_url
