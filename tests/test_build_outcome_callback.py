"""The build-outcome callback must never report a failure it cannot prove.

On 17 Aug 2026 a rebuild of lead 39923c42 published a live site and the row was
written `failed`:

    11:53:44  edge:   Signal timed out                 <- caller aborts at 160s
    11:53:47  callback: reported failed                 <- deploy still running
    11:53:54  deploy: UNVERIFIED .../ateliers--atbs-fr---restauration/

`deploy_site` was awaited via run_in_executor. The abort cancelled that await, but
a running thread cannot be cancelled, so the push finished and returned its URL to
a coroutine that no longer existed. `preview_url` stayed None, and the callback's
`if preview_url: ready else: failed` treated "no URL yet" as "the build failed".
CancelledError is a BaseException, so `except Exception` never set `pipeline_error`
either — leaving the bare "Site generated but deploy failed." default.

That is the outcome POD01-32 exists to prevent, reintroduced through the callback
POD01-32 added. `smart-site-callback` only moves a row out of `building` once, so
the wrong report wins permanently.

Two changes are covered here:
  * the deploy reports from INSIDE its worker thread, so an abandoned await
    cannot discard a live URL
  * an unknown outcome reports NOTHING, leaving the row `building` for the reaper
"""

import asyncio

import pytest

import api


def _drive(coro):
    """Run an async scenario without pytest-asyncio (not installed here)."""
    return asyncio.run(coro)


@pytest.fixture
def rig(monkeypatch):
    """Stub every step of the pipeline and record what the callback posts."""
    posted = []

    def _fake_post(url, secret, lead_id, status, preview_url=None, error=None):
        posted.append({"status": status, "preview_url": preview_url, "error": error})

    monkeypatch.setattr(api, "_post_build_callback", _fake_post)

    monkeypatch.setattr(api, "scrape_site", lambda domain, page_url="": {
        "domain": domain, "url": f"https://{domain}", "business_name": "Test Co",
        "description": "", "services": [], "location": "", "phone": "", "email": "",
        "photos": [], "press": [], "reviews": [],
    })
    monkeypatch.setattr(api, "grade_site", lambda intel: {
        "total": 4, "verdict": "weak", "worth_targeting": True, "breakdown": {},
    })
    monkeypatch.setattr(api, "generate_site", lambda *a, **k: "/tmp/site")
    monkeypatch.setattr(api, "generate_email", lambda *a, **k: {"subject": "s", "body": "b"})
    # Supabase write-back is not what these tests are about.
    monkeypatch.setattr(api, "upsert_lead", lambda **k: None)
    monkeypatch.setattr(api, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "update_engine_queue_result", lambda *a, **k: None)
    return posted


def _pipeline(**over):
    kwargs = dict(
        domain="acme.com", no_deploy=False, offer="Smart Site", cta="Book a Call",
        notes="", known={}, r6=None, lead_id="lead-1",
        callback_url="https://example.test/cb", callback_secret="s3cret", page_url="",
    )
    kwargs.update(over)
    return api.run_pipeline(**kwargs)


# ---------------------------------------------------------------------------
# the regression: caller gives up while the deploy is still running
# ---------------------------------------------------------------------------

def test_abandoned_deploy_reports_ready_not_failed(rig, monkeypatch):
    """The exact 17 Aug failure. A live site must never be reported failed."""
    deploy_started = asyncio.Event()

    def slow_deploy(prospect_id, site_dir):
        # Mimics the real thing: the thread runs to completion even though the
        # coroutine awaiting it has been cancelled.
        loop.call_soon_threadsafe(deploy_started.set)
        import time
        time.sleep(0.3)
        return f"https://pages.test/{prospect_id}/index.html"

    monkeypatch.setattr(api, "deploy_site", slow_deploy)

    async def scenario():
        global loop
        loop = asyncio.get_running_loop()
        gen = _pipeline()
        # Consume until the deploy is in flight, then abandon the generator —
        # this is what Starlette does when the client disconnects.
        async def pump():
            async for _ in gen:
                pass
        task = asyncio.create_task(pump())
        await asyncio.wait_for(deploy_started.wait(), timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await gen.aclose()
        # Give the orphaned worker thread time to finish and report.
        await asyncio.sleep(1.0)

    _drive(scenario())

    assert rig, "nothing was reported at all — the row would sit `building` forever"
    statuses = [p["status"] for p in rig]
    assert "failed" not in statuses, (
        f"reported failed for a site that deployed successfully: {rig}"
    )
    assert statuses.count("ready") == 1, f"expected exactly one ready, got {rig}"
    assert rig[0]["preview_url"] == "https://pages.test/acme-com/index.html"


def test_exactly_one_callback_even_though_two_paths_can_send(rig, monkeypatch):
    """The worker thread and the generator's `finally` must not both report.

    `smart-site-callback` transitions a row out of `building` only once, so a
    second, contradictory report is not merely noise — whichever lands first is
    permanent.
    """
    monkeypatch.setattr(api, "deploy_site", lambda p, s: f"https://pages.test/{p}/")

    async def scenario():
        async for _ in _pipeline():
            pass
        await asyncio.sleep(0.2)

    _drive(scenario())
    assert len(rig) == 1, f"expected one callback, got {rig}"
    assert rig[0]["status"] == "ready"


# ---------------------------------------------------------------------------
# a genuine failure must still be reported as one
# ---------------------------------------------------------------------------

def test_real_deploy_error_still_reports_failed(rig, monkeypatch):
    """Silencing unknown outcomes must not silence known ones."""
    def boom(prospect_id, site_dir):
        raise RuntimeError("GitHub API 422")

    monkeypatch.setattr(api, "deploy_site", boom)

    async def scenario():
        async for _ in _pipeline():
            pass
        await asyncio.sleep(0.2)

    _drive(scenario())
    assert len(rig) == 1, f"expected one callback, got {rig}"
    assert rig[0]["status"] == "failed"
    assert rig[0]["preview_url"] is None


def test_pipeline_error_before_deploy_reports_failed(rig, monkeypatch):
    """A build that dies during intel never reaches a deploy — still a failure."""
    def boom(domain, page_url=""):
        raise ValueError("Could not read https://acme.com — 0 chars of text")

    monkeypatch.setattr(api, "scrape_site", boom)

    async def scenario():
        async for _ in _pipeline():
            pass
        await asyncio.sleep(0.2)

    _drive(scenario())
    assert len(rig) == 1, f"expected one callback, got {rig}"
    assert rig[0]["status"] == "failed"
    assert "Could not read" in (rig[0]["error"] or "")


# ---------------------------------------------------------------------------
# no_deploy callers are unaffected
# ---------------------------------------------------------------------------

def test_no_deploy_never_reports(rig, monkeypatch):
    """Smoke tests and CLI runs must not touch a lead's status."""
    monkeypatch.setattr(api, "deploy_site", lambda p, s: "unused")

    async def scenario():
        async for _ in _pipeline(no_deploy=True):
            pass
        await asyncio.sleep(0.2)

    _drive(scenario())
    assert rig == [], f"no_deploy run reported something: {rig}"
