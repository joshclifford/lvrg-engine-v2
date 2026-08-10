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
# ref moves — that gap is what put 404s in front of prospects. We poll before
# returning, but the window has to fit the caller's budget: leadscraper aborts the
# whole engine call at 135s (build-smart-site ENGINE_TIMEOUT_MS) and a build already
# takes 85-95s. Raise this only if that budget goes up too.
PAGES_VERIFY_TIMEOUT = int(os.environ.get("PAGES_VERIFY_TIMEOUT", "40"))
PAGES_VERIFY_INTERVAL = 4


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
    """Push site to GitHub Pages via Git Data API. No size limits. Returns public URL."""

    print(f"  [deploy] Pushing {prospect_id} via Git Data API...")

    # Read the HTML file
    index_path = os.path.join(site_dir, "index.html")
    with open(index_path, "rb") as f:
        content = f.read()

    # 1. Create a blob with the file content
    blob = _api("POST", "git/blobs", {
        "content": base64.b64encode(content).decode(),
        "encoding": "base64"
    })
    blob_sha = blob["sha"]

    # 2. Get current HEAD commit and its tree
    ref = _api("GET", "git/ref/heads/main")
    head_sha = ref["object"]["sha"]
    head_commit = _api("GET", f"git/commits/{head_sha}")
    base_tree_sha = head_commit["tree"]["sha"]

    # 3. Create a new tree with our file
    tree = _api("POST", "git/trees", {
        "base_tree": base_tree_sha,
        "tree": [
            {
                "path": f"{prospect_id}/index.html",
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha
            }
        ]
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
