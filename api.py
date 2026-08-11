"""
LVRG Engine — FastAPI Server
Wraps the engine pipeline as an HTTP API with SSE streaming.
"""

import asyncio
import json
import os
import sys
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


class BuildRequest(BaseModel):
    domain: str
    no_deploy: bool = False
    offer: str = "Smart Site"
    cta: str = "Book a Call"
    notes: str = ""
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
        intel["review_count"] = known.get("review_count")
        used.append("rating")

    socials = {k: known[k] for k in ("facebook_url", "instagram_url", "linkedin_url")
               if known.get(k)}
    if socials:
        intel["socials"] = socials
        used.append("socials")

    return used


async def run_pipeline(domain: str, no_deploy: bool, offer: str, cta: str, notes: str = "", known: dict = None, r6: Optional[dict] = None) -> AsyncGenerator[str, None]:
    """Run the full engine pipeline, yielding SSE events."""

    loop = asyncio.get_event_loop()

    def emit(type: str, **kwargs):
        # Returns the SSE string — we collect and yield from outside
        pass

    logs = []

    def log(text: str, level: str = "info"):
        line = sse("log", text=text, level=level)
        logs.append(line)

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
        intel = await loop.run_in_executor(None, scrape_site, domain)
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
        yield sse("log", text=f"Score: {grade['total']}/10 — {grade['verdict']}", level="success")
        yield sse("grade", data=grade)

        if not grade["worth_targeting"]:
            yield sse("log", text=f"Score {grade['total']}/10 — noted, building anyway", level="info")

        # ── Step 3: Generate site ────────────────────────────────────
        from slug import make_slug
        prospect_id = make_slug(domain)

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
                preview_url = await loop.run_in_executor(None, deploy_site, prospect_id, site_dir)
                yield sse("log", text=f"Live at {preview_url}", level="success")
            except Exception as e:
                yield sse("log", text=f"Deploy failed: {e} — continuing", level="error")
                preview_url = None
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
        yield sse("result", payload={
            "preview_url": preview_url,
            "email": email_data,
            "intel": intel,
            "grade": grade,
        })
        yield sse("done", status="complete")

    except Exception as e:
        yield sse("log", text=f"Pipeline error: {e}", level="error")
        yield sse("error", text=str(e))
        yield sse("done", status="error")


@app.post("/build")
async def build(req: BuildRequest):
    """Run the full engine pipeline for a domain. Returns SSE stream."""
    domain = req.domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    if not domain:
        raise HTTPException(status_code=400, detail="domain is required")

    return StreamingResponse(
        run_pipeline(domain, req.no_deploy, req.offer, req.cta, req.notes, req.known, req.r6),
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
