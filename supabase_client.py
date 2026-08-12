"""
LVRG Engine — Supabase Client
Saves leads and events to Supabase after each engine run.
"""

import os
import json
import urllib.request
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fwcdiqfsjtwtlmekjqir.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
DEFAULT_BRAND_ID = os.environ.get("LVRG_BRAND_ID", "0be94239-82c7-440e-80ef-171033694fb5")  # LVRG default brand


def _request(method: str, path: str, body: dict = None) -> dict:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"  [supabase] {method} {path} → {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"  [supabase] Error: {e}")
        return None


def upsert_lead(
    domain: str,
    intel: dict,
    grade: dict,
    preview_url: str,
    email_data: dict,
    instantly_lead_id: str = None,
    instantly_campaign_id: str = None,
    offer: str = "Website Rebuild",
    cta: str = "Book a Call",
    status: str = "built",
) -> dict | None:
    """Save or update a lead in Supabase. Returns the lead record."""

    # The domain is stored RAW, deliberately.
    #
    # Canonicalising here (so `https://www.acme.com/` lands as `acme.com`) would
    # fix lm-tool's same-business comparison at the source — but `leads.domain`
    # is JOINED to `engine_queue.domain`, which is written elsewhere and stays
    # raw:
    #   lm-tool/app/dashboard/lead-magnets/page.tsx  `domain=in.(...)`
    #   api.py:167                                   `domain=eq.{domain}`
    # Canonicalising one side alone silently breaks that lookup and the Lead
    # Magnets page loses its subject lines — a worse bug than the one it fixes.
    # Both tables have to migrate together; that is a scheduled change, not a
    # side effect of a write.
    #
    # lm-tool normalises on READ instead — `normalizeDomain` in lib/leads.ts —
    # which costs nothing and touches no stored value.

    lead = {
        "domain": domain,
        "company_name": intel.get("business_name"),
        "email": intel.get("email"),
        "first_name": intel.get("owner_name") or "",
        "phone": intel.get("phone"),
        "offer": offer,
        "cta": cta,
        "preview_url": preview_url,
        "website_score": grade.get("total") if grade else None,
        "status": status,
        "instantly_lead_id": instantly_lead_id,
        "instantly_campaign_id": instantly_campaign_id,
        "brand_id": DEFAULT_BRAND_ID,
        # Structured fields for Instantly custom variables
        "owner_name": intel.get("owner_name") or "",
        "neighborhood": intel.get("neighborhood") or "",
        "business_type": intel.get("business_type") or "",
        "pain_point": intel.get("pain_point") or "",
        "hook": email_data.get("hook") or ("new_site" if (grade.get("total") or 5) <= 5 else "live_chat"),
    }

    # Always INSERT — rebuilds create a new lead row rather than overwriting the
    # previous one, so a rebuild can never destroy outreach history already
    # attached to it (commit dfc6a56). Collapsing those rows back down to one
    # per business is the reader's job, and it now happens in lm-tool:
    # `currentLeadPerDomain` in lib/leads.ts, applied by getLeads() and the
    # dashboard leads page. Keep the DESC ordering there if you touch it.
    result = _request("POST", "leads", lead)

    if result:
        lead_id = result[0]["id"] if isinstance(result, list) else result.get("id")
        print(f"  [supabase] ✓ Lead saved: {domain} (id: {lead_id})")
        return result[0] if isinstance(result, list) else result
    return None


def log_event(lead_id: str, event: str, metadata: dict = None):
    """Log an event for a lead."""
    _request("POST", "lead_events", {
        "lead_id": lead_id,
        "event": event,
        "metadata": metadata or {},
    })


def update_engine_queue_result(domain: str, preview_url: str, email_data: dict):
    """Write preview_url + email_json back to engine_queue after a build.
    Called after site is deployed so the Engine page can restore results on navigation."""
    import urllib.parse
    encoded_domain = urllib.parse.quote(domain, safe='')
    body = {
        "status": "built",
        "preview_url": preview_url,
        "email_json": email_data,
    }
    result = _request("PATCH", f"engine_queue?domain=eq.{encoded_domain}", body)
    if result is not None:
        print(f"  [supabase] ✓ engine_queue write-back: {domain}")
    return result


def update_lead_status(domain: str, status: str, extra: dict = None):
    """Update a lead's status by domain."""
    import urllib.parse
    body = {"status": status}
    if status == "sent":
        body["sent_at"] = "now()"
    if extra:
        body.update(extra)
    # Matches on the RAW domain, the same form upsert_lead stored. See the note
    # there: canonicalising either one alone puts this table out of step with
    # engine_queue.
    # Encode, exactly as update_engine_queue_result above already does. A domain
    # carrying a query string puts a raw `&` into the PostgREST query string,
    # which starts a NEW parameter and silently drops the filter — the PATCH
    # then matches the wrong rows, or none, and returns no error. The lead's
    # status never advances and the outreach can be sent twice.
    encoded_domain = urllib.parse.quote(domain, safe='')
    result = _request("PATCH", f"leads?domain=eq.{encoded_domain}", body)
    if result:
        print(f"  [supabase] ✓ Status updated: {domain} → {status}")
    return result
