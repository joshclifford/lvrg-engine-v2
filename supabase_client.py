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

    # Always INSERT — rebuilds create a new lead row (dedup handled in the app)
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
    body = {"status": status}
    if status == "sent":
        body["sent_at"] = "now()"
    if extra:
        body.update(extra)
    result = _request("PATCH", f"leads?domain=eq.{domain}", body)
    if result:
        print(f"  [supabase] ✓ Status updated: {domain} → {status}")
    return result
