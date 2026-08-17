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
from intel import scrape_site, grade_site
from generator import generate_site, generate_email
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


async def run_pipeline(domain: str, no_deploy: bool, offer: str, cta: str, notes: str = "", known: dict = None, r6: Optional[dict] = None, lead_id: str = "", callback_url: str = "", callback_secret: str = "", page_url: str = "") -> AsyncGenerator[str, None]:
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
    callback_sent = False
    # Two threads can reach the callback: the deploy worker (see
    # _deploy_and_report) and the generator's own `finally`. The lock makes
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
        if preview_url:
            _post_build_callback(callback_url, callback_secret, lead_id, "ready",
                                 preview_url=preview_url, grade=grade_for_callback)
        else:
            _post_build_callback(
                callback_url, callback_secret, lead_id, "failed",
                error=pipeline_error or deploy_error or "Site generated but deploy failed.",
            )

    def _deploy_and_report(prospect_id: str, site_dir: str) -> str:
        """Deploy, then report the outcome — both inside ONE worker thread.

        WHY THIS SHAPE (17 Aug 2026): `deploy_site` used to be awaited on its own
        and the callback sent afterwards by the generator. A client disconnect
        cancels that await, but it CANNOT cancel the thread already running
        inside it — Python has no way to kill a running thread. So the push
        completed, GitHub Pages was polled, the URL was returned... to a
        coroutine that no longer existed. The result was discarded and the
        generator reported `failed` for a site that was live.

        Putting the report in the same thread as the work is what fixes it: the
        thread runs to completion regardless of who is still listening, so
        whatever it observes is what gets reported. Its return value is still
        awaited for the SSE stream, but nothing depends on that await surviving.
        """
        nonlocal callback_sent
        url = deploy_site(prospect_id, site_dir)
        # Claim the callback before sending so the generator's `finally` cannot
        # also fire. Belt and braces — `finally` already declines to report an
        # unknown outcome — but this is the ordering that matters most, because
        # `smart-site-callback` only transitions a row out of `building` once:
        # whichever report lands first wins, and a wrong one cannot be undone.
        if not no_deploy:
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
        return url

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
        intel = await loop.run_in_executor(None, scrape_site, domain, page_url)
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

        # ── Step 3: Generate site ────────────────────────────────────
        # page_url keeps two sub-businesses on one domain from sharing a preview
        # folder and overwriting each other's live page (POD01-34). Empty for a
        # root-domain lead, which yields the identical slug to before.
        prospect_id = make_slug(domain, page_url)

        yield sse("log", text="Generating Smart Site with Claude...", level="info")
        if notes:
            yield sse("log", text=f"Notes: {notes}", level="info")
        if r6:
            yield sse("log", text="Grounding copy in R6 audit's weakest pillar", level="info")
        site_dir = await loop.run_in_executor(None, generate_site, intel, prospect_id, notes, r6)
        yield sse("log", text="Site generated", level="success")

        # ── Step 4: Deploy ───────────────────────────────────────────
        preview_url = None
        if not no_deploy:
            yield sse("log", text="Deploying to GitHub Pages...", level="info")
            try:
                # _deploy_and_report, not deploy_site: the thread reports the
                # outcome itself, so a caller that has already given up cannot
                # cause a live site to be recorded as failed.
                preview_url = await loop.run_in_executor(None, _deploy_and_report, prospect_id, site_dir)
                yield sse("log", text=f"Live at {preview_url}", level="success")
            except Exception as e:
                yield sse("log", text=f"Deploy failed: {e} — continuing", level="error")
                preview_url = None
                # Record it: a CAUGHT exception is a proven failure, and without
                # this the callback would classify it as "unknown" and stay
                # silent, leaving the lead to be reaped 15 minutes later instead
                # of failing immediately. CancelledError never lands here — it is
                # a BaseException — which is exactly the discrimination we want.
                deploy_error = f"Deploy failed: {e}"
        else:
            preview_url = f"[local] {site_dir}/index.html"

        # ── Step 5: Generate email ───────────────────────────────────
        yield sse("log", text="Writing outreach messaging...", level="info")
        email_data = await loop.run_in_executor(None, generate_email, intel, grade, prospect_id, r6)
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
        yield sse("result", payload={
            "preview_url": preview_url,
            "email": email_data,
            "intel": intel,
            "grade": grade,
        })
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

    return StreamingResponse(
        run_pipeline(domain, req.no_deploy, req.offer, req.cta, req.notes, req.known, req.r6,
                     req.lead_id, req.callback_url, req.callback_secret, page_url),
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

    return {"reply": response.content[0].text.strip()}


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
