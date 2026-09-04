"""
LVRG Engine — FastAPI Server
Wraps the engine pipeline as an HTTP API with SSE streaming.
"""

import asyncio
import json
import os
import sys
import threading
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Engine modules
import cost
from claude_text import first_text
from intel import scrape_site, grade_site
from generator import generate_site, generate_multi_page_site, generate_email
from pages import plan_pages
from deploy import deploy_site
from slug import canonical_domain, make_slug
from supabase_client import upsert_lead, log_event, update_engine_queue_result

# Interactive docs are off unless ENABLE_DOCS is set. This service is public and
# /chat is unauthenticated, so /openapi.json is a map of how to spend our credit.
_DOCS = os.environ.get("ENABLE_DOCS", "").lower() in ("1", "true", "yes")

# Version distinguishes v2 from v1 — both previously reported 1.2.0 on /health,
# so there was no way to tell which engine served a given build.
app = FastAPI(
    title="LVRG Engine API v2",
    version="2.0.0",
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)


@app.on_event("startup")
async def run_migrations():
    """Add any missing columns to engine_queue on startup."""
    import urllib.request, urllib.error, json as _json
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fwcdiqfsjtwtlmekjqir.supabase.co")
    SERVICE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not SERVICE_KEY:
        print("[startup] SUPABASE_KEY not set, skipping column check")
        return
    # Try to SELECT the new columns — if they don't exist PostgREST returns a 400
    # We add them by calling a stored procedure if it exists, otherwise skip gracefully
    try:
        url = f"{SUPABASE_URL}/rest/v1/engine_queue?select=preview_url,email_json&limit=0"
        req = urllib.request.Request(url, headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
        })
        urllib.request.urlopen(req)
        print("[startup] engine_queue columns OK")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "does not exist" in body:
            print("[startup] engine_queue missing columns — please run migration SQL:")
            print("  ALTER TABLE engine_queue")
            print("    ADD COLUMN IF NOT EXISTS preview_url text,")
            print("    ADD COLUMN IF NOT EXISTS email_json jsonb;")
        else:
            print(f"[startup] column check: {e.code} {body[:200]}")
    except Exception as e:
        print(f"[startup] migration check skipped: {e}")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _post_build_callback(
    callback_url: str,
    callback_secret: str,
    lead_id: str,
    status: str,
    preview_url: str = None,
    error: str = None,
    grade: dict = None,
    ai_cost: dict = None,
) -> None:
    """Tell the caller how the build actually ended.

    WHY (13 Aug 2026): leadscraper's build-smart-site waits on /build with a
    timeout and used to treat that timeout as failure. Real builds run 106-150s
    against its 160s wait, so builds that had ALREADY SUCCEEDED were written off
    — one site was deployed 365ms before the caller gave up, and the lead was
    marked failed and refunded while the page sat live on GitHub Pages.

    It cannot simply read our result: this engine writes to a DIFFERENT Supabase
    project (lm-tool's `leads`/`engine_queue`) and has no access to leadscraper's
    `businesses` table. So we report over HTTP instead, which also means no
    leadscraper service-role key ever has to live on Railway.

    NEVER RAISES. This engine is shared with lm-tool, whose builds must not fail
    because a leadscraper endpoint is down or misconfigured. Best-effort only.
    """
    if not callback_url or not callback_secret or not lead_id:
        return  # caller didn't ask for a callback (lm-tool, smoke tests, CLI)
    try:
        import urllib.request
        payload = {"lead_id": lead_id, "status": status}
        if preview_url:
            payload["preview_url"] = preview_url
        if error:
            payload["error"] = str(error)[:500]
        # The score, so a build resolved by callback keeps it. Until 17 Aug the
        # grade was written only by build-smart-site after it consumed the SSE
        # stream, so a build that TIMED OUT — the entire case this callback
        # exists for — landed `ready` with a permanently null grade and no badge
        # in the UI. Sent as the total plus the per-pillar breakdown the badge
        # tooltip expands; the receiver validates both and drops anything odd.
        if isinstance(grade, dict):
            total = grade.get("total")
            if isinstance(total, (int, float)):
                payload["grade"] = total
            payload["grade_breakdown"] = grade
        # What this build cost us at Anthropic, in US cents. Additive: a
        # receiver that predates it simply ignores the key, and lm-tool passes
        # no callback_url at all so it never sends one.
        if isinstance(ai_cost, dict):
            total_cents = ai_cost.get("total_cents")
            if isinstance(total_cents, (int, float)):
                payload["ai_cost_cents"] = total_cents
            payload["ai_cost_breakdown"] = ai_cost
        req = urllib.request.Request(
            callback_url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-callback-secret": callback_secret,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            res.read()
        print(f"  [callback] ✓ reported {status} for lead {lead_id}")
    except Exception as e:
        # Logged, never raised. If this fails the lead stays `building` on the
        # caller's side and their reaper refunds it — degraded, not broken.
        print(f"  [callback] failed to report {status} for lead {lead_id}: {e}")


# The variant lands in the slug, and serve-smart-site rejects anything over 128
# characters — a longer slug deploys fine and then 400s on a link already sent.
# The slug builder budgets its own suffix too; this just stops an absurd value
# arriving in the first place.
MAX_VARIANT_CHARS = 60


class BuildRequest(BaseModel):
    domain: str
    no_deploy: bool = False
    offer: str = "Smart Site"
    cta: str = "Book a Call"
    notes: str = ""
    # Completion callback (leadscraper). All three must be present or the
    # callback is skipped entirely — every other caller omits them and is
    # unaffected.
    lead_id: str = ""
    callback_url: str = ""
    callback_secret: str = ""
    # The lead's own page, path included, when `domain` alone is not the business
    # (POD01-34). Additive and same-host-only — see the check in /build. Empty
    # means "scrape the root", which is exactly what every caller did before.
    page_url: str = ""
    # Distinguishes branches of a chain, which share one domain and have no
    # distinguishing path — every Better Buzz lists betterbuzzcoffee.com, so all
    # of them slugged to `betterbuzzcoffee-com` and the second build replaced the
    # first's live page. leadscraper sends the business name, and only for a
    # lead that has no published preview yet, so nothing already emailed moves.
    # Additive: empty gives the identical slug to before.
    variant: str = ""
    # Known facts the caller already holds. leadscraper enriches every lead via
    # Apify + Hunter, so it has the phone, email, address, socials and a real
    # Google rating before the engine starts. Re-deriving them from a scrape is
    # strictly worse: the site often doesn't list them, and we published pages
    # with a blank contact section for businesses whose number we'd had on file
    # for days. Anything passed here wins over anything scraped.
    known: dict = {}
    # Optional R6 audit pillar breakdown from the caller (leadscraper's
    # build-smart-site edge fn). None for any caller that doesn't send one
    # (MCP tool, smoke_test.sh, direct API calls) — generator.py falls back
    # to its existing Claude-extracted pain_point when this is absent.
    r6: Optional[dict] = None
    # Opt into a multi-page build (Home/About/Services/Contact, planned by
    # pages.plan_pages) instead of today's single index.html. Default off —
    # every existing caller (leadscraper's current payload, run_engine.py,
    # smoke tests) omits this and is completely unaffected.
    multi_page: bool = False


class ChatRequest(BaseModel):
    message: str
    business_name: str
    intel: dict  # full intel object baked into each widget
    history: list = []  # [{"role": "user"|"assistant", "content": "..."}]


def sse(type: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type, **kwargs})}\n\n"


def _merge_known(intel: dict, known: dict) -> list:
    """Overlay caller-supplied facts onto scraped intel. Returns what was used.

    Only non-empty values overwrite, so a caller sending partial data can't
    blank out something the scrape did find.
    """
    used = []

    for field in ("phone", "email", "hours", "owner_name", "neighborhood"):
        value = (known.get(field) or "").strip() if isinstance(known.get(field), str) else known.get(field)
        if value:
            intel[field] = value
            used.append(field)

    # Address is the app's column name; the engine calls it location.
    location = (known.get("address") or known.get("location") or "")
    if isinstance(location, str) and location.strip():
        intel["location"] = location.strip()
        used.append("location")

    # Real rating from Google Maps. The generator shows it as a stat and is
    # told never to invent one, so passing it is the only way a page gets stars.
    if known.get("rating") is not None:
        intel["rating"] = known["rating"]
        # Only overwrite the count when we actually have one. Writing it
        # unconditionally passed None straight through to the generator prompt,
        # which rendered it as the literal string: "rated 4.5★ from None
        # reviews" — on a page carrying the prospect's own branding, labelled to
        # the model as a real social-proof stat. Apify returns a rating without
        # a count for some Maps listings, so this is not a rare path.
        if known.get("review_count") is not None:
            intel["review_count"] = known["review_count"]
        used.append("rating")

    socials = {k: known[k] for k in ("facebook_url", "instagram_url", "linkedin_url")
               if known.get(k)}
    if socials:
        intel["socials"] = socials
        used.append("socials")

    return used


async def run_pipeline(domain: str, no_deploy: bool, offer: str, cta: str, notes: str = "", known: dict = None, r6: Optional[dict] = None, lead_id: str = "", callback_url: str = "", callback_secret: str = "", page_url: str = "", multi_page: bool = False, variant: str = "") -> AsyncGenerator[str, None]:
    """Run the full engine pipeline, yielding SSE events."""

    loop = asyncio.get_event_loop()

    def emit(type: str, **kwargs):
        # Returns the SSE string — we collect and yield from outside
        pass

    logs = []

    def log(text: str, level: str = "info"):
        line = sse("log", text=text, level=level)
        logs.append(line)

    # ── Callback state ───────────────────────────────────────────────────────
    # These live OUTSIDE the try so the finally block can still read them after
    # the generator is torn down mid-flight.
    preview_url = None
    pipeline_error = None
    # Set only when deploy_site RAISED. Distinct from pipeline_error because the
    # deploy is deliberately non-fatal ("— continuing"), so its exception is
    # swallowed and never reaches the outer handler — yet it is still a KNOWN
    # failure, unlike an await that was merely cancelled. That difference is the
    # whole point: a caught exception means the deploy definitely failed, a
    # CancelledError means we simply stopped watching.
    deploy_error = None
    # The grade, mirrored out here for the callback. A separate name from the
    # `grade` local inside the try because both callbacks can fire BEFORE that
    # local is assigned — a build that dies during intel never reaches grading,
    # and reading an unassigned local from a closure is an UnboundLocalError,
    # which would lose the callback entirely at the moment it matters.
    grade_for_callback = None
    # Set before _build_and_report runs (plan_pages is fast/local, done on the
    # event loop) so the worker thread below can read it as a closure.
    pages_plan = None
    # What this build spent at Anthropic. Lives out here for the same reason as
    # everything else in this block: the callback fires from `finally`, and on a
    # caller timeout that is the ONLY thing that still reports — a meter scoped
    # inside the try would be unreadable exactly when the number matters. One
    # per pipeline run, never module-level: builds run concurrently and
    # multi-page generation fans out further, so a shared meter would bill one
    # prospect's pages to another.
    meter = cost.CostMeter()
    callback_sent = False
    # Two threads can reach the callback: the build worker (see
    # _build_and_report) and the generator's own `finally`. The lock makes
    # "first one wins" actually true instead of probably true.
    callback_lock = threading.Lock()

    def _send_callback_once():
        """Report the build outcome to the caller. Safe to call repeatedly.

        MUST be synchronous. This is invoked from `finally`, which on a client
        disconnect runs while GeneratorExit is propagating — awaiting there
        raises "async generator ignored GeneratorExit" and the callback would be
        lost precisely when it matters most.

        WON'T REPORT A FAILURE IT CANNOT PROVE (17 Aug 2026). The old version
        was `if preview_url: ready else: failed`, which treated "no URL yet" as
        "the build failed". That is wrong whenever the pipeline was interrupted
        rather than broken, and it cost us a real site:

          11:53:44  edge:   Signal timed out          <- caller aborts at 160s
          11:53:47  callback: reported failed          <- deploy still running
          11:53:54  deploy: UNVERIFIED .../ateliers--atbs-fr---restauration/

        The site went live and the row said `failed` — the exact outcome
        POD01-32 exists to prevent, reintroduced through the callback that fix
        added. `await run_in_executor(deploy_site, ...)` had been CANCELLED, and
        CancelledError is a BaseException, so `except Exception` never saw it and
        `pipeline_error` stayed None — leaving the bare "failed" default.

        So an unknown outcome now sends NOTHING. The row stays `building` and the
        5-minute reaper settles it, which is the same principle POD01-32 set:
        an interruption is not a failure.
        """
        nonlocal callback_sent
        if no_deploy:
            return
        with callback_lock:
            if callback_sent:
                return
            if not preview_url and not pipeline_error and not deploy_error:
                # Interrupted, not failed. Say nothing rather than lie; the
                # deploy worker may still be about to report the truth.
                print(f"  [callback] outcome unknown for lead {lead_id} — "
                      f"leaving it `building` for the reaper, not reporting failed")
                return
            callback_sent = True
        # The spend is reported on BOTH outcomes. A failed build still burned
        # every token it spent before it died, and a COGS dashboard that only
        # counts successes understates what the feature actually costs.
        ai_cost = meter.summary()
        if preview_url:
            _post_build_callback(callback_url, callback_secret, lead_id, "ready",
                                 preview_url=preview_url, grade=grade_for_callback,
                                 ai_cost=ai_cost)
        else:
            _post_build_callback(
                callback_url, callback_secret, lead_id, "failed",
                error=pipeline_error or deploy_error or "Site generated but deploy failed.",
                ai_cost=ai_cost,
            )

    def _build_and_report(prospect_id: str):
        """Generate (single- or multi-page) AND deploy AND report the outcome
        — all three inside ONE worker thread.

        WHY THIS SHAPE, WIDENED FROM DEPLOY-ONLY (24 Aug 2026): this used to
        wrap deploy_site alone, because deploy was the one step slow enough
        to routinely outlive a caller that had already given up (17 Aug —
        see the git history on this function). That stopped being true once
        multi-page builds shipped: generating up to
        PAGE_GENERATION_CONCURRENCY pages is now often the slower half, and
        it used to run as a bare `await run_in_executor(generate_..., ...)`
        with no self-report of its own.

        A client disconnect cancels that await, but — same as deploy — it
        CANNOT cancel the thread already running inside it. So the pages
        kept generating in the background, `finally` ran immediately with
        nothing to report yet (an honestly unknown outcome, correctly left
        silent — see _send_callback_once), and when generation finished
        there was no code path left that would ever call deploy_site or
        send a callback: the coroutine that was going to do both had already
        been torn down. The site was fully generated, on disk, in a
        container that will eventually recycle it — and never went live,
        never got reported, never refunded until the 15-minute reaper caught
        it. Real generated work, thrown away, silently.

        Putting generation, deploy, AND the report in the same thread closes
        that the same way the 17 Aug fix closed it for deploy alone: the
        thread runs to completion regardless of who is still listening, so
        whatever it observes is what gets reported. Its return value is
        still awaited for the SSE stream, but nothing depends on that await
        surviving.

        Deploy failure stays non-fatal here (mirrors the prior "Deploy
        failed: ... — continuing" behavior) so a generated-but-undeployed
        site still gets its outreach email drafted downstream. A GENERATION
        failure has no site to deploy and nothing to email about, so it
        propagates and ends the build — same as before this change.
        """
        nonlocal callback_sent, deploy_error
        if multi_page:
            paths = generate_multi_page_site(intel, prospect_id, pages_plan, notes, r6,
                                             meter=meter, photo_assets=photo_assets)
            site_dir_local = os.path.dirname(next(iter(paths.values())))
        else:
            paths = None
            site_dir_local = generate_site(intel, prospect_id, notes, r6,
                                           meter=meter, photo_assets=photo_assets)

        if no_deploy:
            return f"[local] {site_dir_local}/index.html", paths

        try:
            url = deploy_site(prospect_id, site_dir_local)
        except Exception as e:
            # Known, proven failure — unlike a cancelled await, this is safe
            # to record even though nothing here reports it directly; the
            # generator's own `finally` reports it IF the caller is still
            # connected (same as always). If the caller has already gone,
            # this is the one gap this change does not additionally close —
            # deploy failing specifically in that window was already
            # unreported before today, and the reaper is the existing
            # backstop for it, same as it always was.
            deploy_error = f"Deploy failed: {e}"
            return None, paths

        # Claim the callback before sending so the generator's `finally` cannot
        # also fire. Belt and braces — `finally` already declines to report an
        # unknown outcome — but this is the ordering that matters most, because
        # `smart-site-callback` only transitions a row out of `building` once:
        # whichever report lands first wins, and a wrong one cannot be undone.
        with callback_lock:
            already = callback_sent
            callback_sent = True
        if not already:
            # The claim above happens BEFORE this call, so a throw here would
            # consume the callback and leave `finally` unable to retry.
            # _post_build_callback is documented never to raise, but relying
            # on that silently is how a report gets lost at the one moment it
            # matters — so make the failure loud. The 5-minute reaper is the
            # backstop if it ever fires.
            try:
                _post_build_callback(callback_url, callback_secret, lead_id, "ready",
                                     preview_url=url, grade=grade_for_callback)
            except Exception as _e:
                print(f"  [callback] FAILED to report ready for lead {lead_id}: {_e} — "
                      f"the site IS live at {url}; the reaper will fail+refund it wrongly")
        return url, paths

    try:
        # ── Step 0: Fetch queue contact data (Scout may have found email/phone) ──
        queue_contact = {}
        try:
            import urllib.request as _ur, urllib.parse as _up
            _key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
            _url = f"https://fwcdiqfsjtwtlmekjqir.supabase.co/rest/v1/engine_queue?select=email,phone&domain=eq.{_up.quote(domain, safe='')}&limit=1"
            _req = _ur.Request(_url, headers={"apikey": _key, "Authorization": f"Bearer {_key}"})
            with _ur.urlopen(_req) as _res:
                _rows = __import__('json').loads(_res.read().decode())
                if _rows: queue_contact = _rows[0]
        except Exception:
            pass

        # ── Step 1: Intel ────────────────────────────────────────────
        yield sse("log", text=f"Reading {domain}...", level="info")
        # lambda, not positional args: run_in_executor cannot forward kwargs,
        # and `meter` has to arrive as one so every other caller of scrape_site
        # (lm-tool, run_engine.py, the tests) keeps its existing signature.
        intel = await loop.run_in_executor(
            None, lambda: scrape_site(domain, page_url, meter=meter)
        )
        # Lift the downloaded photo bytes straight off the dict, BEFORE `intel`
        # is emitted or stored anywhere (POD01-124). Three lines below it goes
        # out over SSE, and further down into result_payload — from where
        # leadscraper writes it to `businesses.smart_site_intel`, a column its
        # All Leads query selects for every row on screen. Measured: four real
        # photos take one lead's intel from 6.7 KB to 771 KB, and a 25-row page
        # from 166 KB to 18.8 MB. The assets are needed only by the generator,
        # so they travel as their own argument and never on the record.
        #
        # Popped HERE rather than stripped at each exit: this is the one place
        # every build passes through, and a strip-per-boundary scheme is how
        # the next boundary gets missed.
        photo_assets = intel.pop("photo_assets", None)

        # Prefer Scout-found email/phone over scraped (Scout finds real contact emails)
        if queue_contact.get("email"): intel["email"] = queue_contact["email"]
        if queue_contact.get("phone"): intel["phone"] = queue_contact["phone"]

        # Caller-supplied facts beat anything we scraped — they came from Apify
        # and Hunter, not from guessing at a page that may not list them.
        merged = _merge_known(intel, known or {})
        if merged:
            yield sse("log", text=f"Using known data from app: {', '.join(merged)}", level="info")
        yield sse("log", text=f"Got intel for {intel['business_name']}", level="success")
        yield sse("intel", data=intel)

        # ── Step 2: Grade ────────────────────────────────────────────
        yield sse("log", text="Grading site...", level="info")
        grade = await loop.run_in_executor(None, grade_site, intel)
        grade_for_callback = grade
        yield sse("log", text=f"Score: {grade['total']}/10 — {grade['verdict']}", level="success")
        yield sse("grade", data=grade)

        if not grade["worth_targeting"]:
            yield sse("log", text=f"Score {grade['total']}/10 — noted, building anyway", level="info")

        # ── Step 3 + 4: Generate (single- or multi-page) AND deploy ───
        # page_url keeps two sub-businesses on one domain from sharing a preview
        # folder and overwriting each other's live page (POD01-34); variant does
        # the same for chain branches, which share a domain AND have no path.
        # Both empty for an ordinary root-domain lead, which yields the identical
        # slug to before.
        prospect_id = make_slug(domain, page_url, variant)

        yield sse("log", text="Generating Smart Site with Claude...", level="info")
        if notes:
            yield sse("log", text=f"Notes: {notes}", level="info")
        if r6:
            yield sse("log", text="Grounding copy in R6 audit's weakest pillar", level="info")

        if multi_page:
            pages_plan = plan_pages(intel)
            yield sse("log", text=f"Planned {len(pages_plan)} pages: {', '.join(p['title'] for p in pages_plan)}", level="info")
        if not no_deploy:
            yield sse("log", text="Deploying to GitHub Pages...", level="info")

        # _build_and_report, not separate generate/deploy awaits: generation
        # and deploy now run inside ONE worker thread that reports its own
        # outcome, so a caller that has already given up — whether generation
        # or deploy was what it was waiting on — cannot cause a site that
        # finishes after it left to go unreported. See _build_and_report's
        # docstring for the incident this closes.
        preview_url, site_paths = await loop.run_in_executor(None, _build_and_report, prospect_id)
        # Reaching this line means generation succeeded — an exception from
        # inside _build_and_report's generation half would have skipped past
        # it to the `except` below. Kept as its own log line (rather than
        # folded into "Live at ...") because smoke_test.sh greps for this
        # exact text to confirm Claude generation happened independently of
        # deploy, which no_deploy=true smoke runs skip entirely.
        yield sse("log", text="Site generated", level="success")
        if preview_url:
            yield sse("log", text=f"Live at {preview_url}", level="success")
        elif deploy_error:
            # Generation succeeded, deploy did not — non-fatal, matching the
            # prior behavior: still write outreach email below rather than
            # discard a generated-but-undeployed site's context entirely.
            yield sse("log", text=f"{deploy_error} — continuing", level="error")

        # ── Step 5: Generate email ───────────────────────────────────
        yield sse("log", text="Writing outreach messaging...", level="info")
        email_data = await loop.run_in_executor(
            None, lambda: generate_email(intel, grade, prospect_id, r6, meter=meter)
        )
        yield sse("log", text="Messaging ready", level="success")

        # ── Step 6: Save to Supabase (skip on no_deploy / smoke test runs) ──
        if no_deploy:
            yield sse("log", text="Skipping Supabase save (test mode)", level="dim")
        else:
            yield sse("log", text="Saving to Supabase...", level="info")
            try:
                sb_lead = await loop.run_in_executor(
                    None,
                    lambda: upsert_lead(
                        domain=domain,
                        intel=intel,
                        grade=grade,
                        preview_url=preview_url,
                        email_data=email_data,
                        instantly_lead_id=None,
                        instantly_campaign_id=None,
                        offer=offer,
                        cta=cta,
                        status="built",
                    )
                )
                if sb_lead:
                    await loop.run_in_executor(
                        None,
                        lambda: log_event(sb_lead["id"], "site_built", {
                            "preview_url": preview_url,
                            "score": grade.get("total"),
                        })
                    )
            except Exception as e:
                yield sse("log", text=f"Supabase save failed: {e}", level="error")

            # ── Step 6b: Write-back preview_url + email_json to engine_queue ──
            try:
                await loop.run_in_executor(
                    None,
                    lambda: update_engine_queue_result(domain, preview_url, email_data)
                )
            except Exception as e:
                yield sse("log", text=f"Queue write-back skipped: {e}", level="dim")

        # ── Done ─────────────────────────────────────────────────────
        # NOTE: the outcome callback is NOT sent here. It fires from `finally`
        # below — see the comment there for why this is the only placement that
        # survives the case it exists to handle.
        # Reported here AND on the callback, because the two paths are read by
        # different writers: on a normal build leadscraper consumes this stream
        # and writes the row itself, and the callback that follows is a no-op
        # ("no longer building"). Only a build that outran the caller's wait is
        # resolved by the callback. Sending the cost on just one of them would
        # lose it for whichever path happened to win.
        ai_cost = meter.summary()
        print(f"  [cost] build spent {ai_cost['total_cents']:.4f} cents across "
              f"{ai_cost['calls']} calls "
              f"({ai_cost['input_tokens']} in / {ai_cost['output_tokens']} out)")
        result_payload = {
            "preview_url": preview_url,
            "email": email_data,
            "intel": intel,
            "grade": grade,
            "ai_cost_cents": ai_cost["total_cents"],
            "ai_cost_breakdown": ai_cost,
        }
        # Additive — `preview_url` stays the homepage either way, so any
        # existing consumer (leadscraper's smart_site_url column) needs no
        # change. `pages` only appears for a multi_page build; a caller that
        # doesn't know the key simply never looks at it.
        if site_paths is not None and preview_url:
            base = preview_url.rsplit("/", 1)[0]
            result_payload["pages"] = {
                slug: f"{base}/{os.path.basename(p)}" for slug, p in site_paths.items()
            }
        yield sse("result", payload=result_payload)
        yield sse("done", status="complete")

    except Exception as e:
        pipeline_error = str(e)
        yield sse("log", text=f"Pipeline error: {e}", level="error")
        yield sse("error", text=str(e))
        yield sse("done", status="error")

    finally:
        # ── Report the outcome to the caller — ALWAYS ────────────────────────
        #
        # This block, not a step in the try, is the only correct home for the
        # callback. run_pipeline is an async generator driven by
        # StreamingResponse: when the caller's HTTP connection drops, Starlette
        # stops iterating and execution NEVER returns to the body — the next
        # `yield` raises GeneratorExit and everything after it is skipped.
        #
        # That is exactly the scenario the callback exists for. leadscraper's
        # build-smart-site aborts its fetch at 160s; this pipeline regularly
        # runs longer (13 Aug: site deployed at 164s). Observed live — the
        # engine logged `[deploy] ... UNVERIFIED https://...` at 12:17:53, the
        # caller had already disconnected at ~12:17:49, and the callback that
        # sat after the deploy step never ran. The site went live, the lead
        # stayed `building`, and only the reaper eventually cleared it.
        #
        # `finally` still runs while GeneratorExit propagates, so the report
        # survives the disconnect. It must stay synchronous: awaiting here
        # raises "async generator ignored GeneratorExit".
        try:
            _send_callback_once()
        except Exception as _cb_err:
            print(f"  [callback] unexpected error while reporting outcome: {_cb_err}")


@app.post("/build")
async def build(req: BuildRequest):
    """Run the full engine pipeline for a domain. Returns SSE stream."""
    domain = req.domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    if not domain:
        raise HTTPException(status_code=400, detail="domain is required")

    # POD01-34: the sub-page to scrape, if the caller named one. `domain` above
    # is left flattened on purpose — the slug, the engine_queue join and every
    # log key derive from it.
    #
    # Same host only, and that is a security requirement rather than tidiness:
    # /build takes no auth and CORS is open, so `domain` is already an
    # unauthenticated fetch primitive — but it can only ever reach a root. A
    # page_url free to name a different host would widen that to arbitrary paths
    # on arbitrary hosts. Pinning it to `domain` means this field can reach
    # nothing the request could not already reach.
    #
    # canonical_domain on BOTH sides, not string equality: the caller strips a
    # leading `www.` when it derives `domain`, so `www.acme.com/x` against
    # `acme.com` is the normal case, not the exception.
    page_url = (req.page_url or "").strip()
    if page_url and canonical_domain(page_url) != canonical_domain(domain):
        print(f"  [build] ignoring page_url — host does not match domain {domain}")
        page_url = ""

    # Unlike page_url there is nothing to validate against the domain: variant is
    # a folder-name discriminator only, never fetched. Trimmed and length-capped
    # because it lands in the slug, which serve-smart-site rejects over 128 chars.
    variant = (req.variant or "").strip()[:MAX_VARIANT_CHARS]

    return StreamingResponse(
        run_pipeline(domain, req.no_deploy, req.offer, req.cta, req.notes, req.known, req.r6,
                     req.lead_id, req.callback_url, req.callback_secret, page_url, req.multi_page,
                     variant),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat")
async def chat(req: ChatRequest):
    """AI chat endpoint for Smart Site widgets. Responds as the business."""
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)

    # Build system prompt from scraped intel
    intel = req.intel
    system = f"""You are the AI assistant for {intel.get('business_name', 'this business')}.
Answer questions warmly and helpfully as a knowledgeable team member.
Keep responses concise — 1-3 sentences. Never say you are an AI unless directly asked.

BUSINESS DETAILS:
- Name: {intel.get('business_name', '')}
- Description: {intel.get('description', '')}
- Services: {', '.join(intel.get('services') or [])}
- Location: {intel.get('location', '')}
- Phone: {intel.get('phone', '')}
- Hours: {intel.get('hours', '')}
- Social proof: {intel.get('social_proof', '')}
- Main CTA: {intel.get('cta_angle', 'contact us')}

If asked about booking, reservations, or appointments, direct them to call or visit.
If you don't know something specific, say you'll have the team follow up."""

    messages = req.history[-10:] + [{"role": "user", "content": req.message}]

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        system=system,
        messages=messages,
    )

    return {"reply": first_text(response).strip()}


@app.post("/migrate")
async def migrate():
    """Admin endpoint: add missing columns to engine_queue.
    Requires SUPABASE_SERVICE_KEY env var with DDL privileges.
    Since PostgREST can't run DDL, this returns the SQL to run manually."""
    sql = (
        "ALTER TABLE engine_queue "
        "ADD COLUMN IF NOT EXISTS preview_url text, "
        "ADD COLUMN IF NOT EXISTS email_json jsonb;"
    )
    # Check if columns already exist
    import urllib.request, urllib.error
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fwcdiqfsjtwtlmekjqir.supabase.co")
    KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
    try:
        url = f"{SUPABASE_URL}/rest/v1/engine_queue?select=preview_url,email_json&limit=0"
        req = urllib.request.Request(url, headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
        urllib.request.urlopen(req)
        return {"status": "columns_exist", "message": "preview_url and email_json already present"}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "does not exist" in body:
            return {"status": "migration_needed", "sql": sql,
                    "instructions": "Run this SQL in Supabase dashboard > SQL Editor"}
        return {"status": "error", "detail": body[:300]}


@app.get("/health")
async def health():
    # Read from the app so the two engines can never report the same version again.
    return {"status": "ok", "service": app.title, "version": app.version}


@app.get("/")
async def root():
    return {"service": app.title, "endpoints": ["/build", "/health"]}
