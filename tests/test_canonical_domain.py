"""One definition of "the same domain", shared by the engine and lm-tool.

lm-tool decides whether two rows are the same business by comparing
`leads.domain`. Stored raw, `https://www.acme.com/` and `acme.com` are two
businesses, and a reply could be stamped on the row the dashboard hides.

Measured 2026-08-12 against lm-tool's live table: of 142 rows, 6 carried a
protocol, 4 a `www.`, and 6 a trailing slash.

The fix is applied on READ (normalizeDomain in lm-tool/lib/leads.ts), not on
write — see the note further down for why the write path deliberately stays
raw. `canonical_domain` here mirrors that TypeScript function and is used by
`make_slug`; if the two definitions drift, the split comes straight back.
"""

import inspect

import pytest

import supabase_client
from slug import canonical_domain, make_slug


@pytest.mark.parametrize("raw", [
    "acme.com",
    "ACME.COM",
    "  acme.com  ",
    "http://acme.com",
    "https://acme.com",
    "https://www.acme.com",
    "https://www.acme.com/",
    "acme.com/",
    "acme.com/some/path",
    "acme.com/?utm_source=x&y_source=z",
    "acme.com#section",
    "acme.com:8080",
    "user@acme.com",
    "acme.com.",
    "  HTTPS://WWW.ACME.COM/?a=1#b  ",
    "www.www.acme.com",
])
def test_every_spelling_reduces_to_one_domain(raw):
    assert canonical_domain(raw) == "acme.com"


@pytest.mark.parametrize("a,b", [
    ("acme.com", "acme.net"),
    ("acme.com", "sub.acme.com"),
    ("acme.com", "notacme.com"),
    ("acme.com", "acme.com.au"),
])
def test_genuinely_different_domains_stay_different(a, b):
    assert canonical_domain(a) != canonical_domain(b)


def test_www_is_not_stripped_off_a_real_registration():
    assert canonical_domain("www.com") == "www.com"


@pytest.mark.parametrize("junk", ["", "   ", None, "https://", "www."])
def test_non_domains_return_empty_for_the_caller_to_handle(junk):
    assert canonical_domain(junk) == ""


def test_make_slug_uses_the_same_canonicaliser():
    """Two copies of these rules is how three slug implementations drifted."""
    assert "canonical_domain(domain)" in inspect.getsource(make_slug)


# --------------------------------------------------------------------------
# the stored column stays RAW — on purpose
# --------------------------------------------------------------------------
#
# Canonicalising `leads.domain` on write looks like the obvious fix and was
# briefly implemented. It is wrong: `leads.domain` is JOINED to
# `engine_queue.domain`, which is written elsewhere and stays raw —
#
#     lm-tool/app/dashboard/lead-magnets/page.tsx   domain=in.(...)
#     api.py:167                                    domain=eq.{domain}
#
# — so canonicalising one side alone silently breaks that lookup and the Lead
# Magnets page loses its subject lines. Worse than the bug it fixes, and silent.
# lm-tool normalises on READ instead. These tests exist so the "obvious fix"
# does not get re-applied without migrating both tables together.

def test_upsert_lead_does_not_canonicalise_the_stored_domain():
    src = inspect.getsource(supabase_client.upsert_lead)
    assert "canonical_domain" not in src, (
        "leads.domain was canonicalised on write — this desynchronises it from "
        "engine_queue.domain and breaks the Lead Magnets subject-line lookup. "
        "Migrate both tables together or leave both raw."
    )


def test_update_lead_status_matches_the_same_raw_form():
    """An exact `eq.` match, so it must use the form upsert_lead stored."""
    src = inspect.getsource(supabase_client.update_lead_status)
    assert "canonical_domain" not in src


def test_engine_queue_lookup_is_left_alone():
    src = inspect.getsource(supabase_client.update_engine_queue_result)
    assert "canonical_domain" not in src


def test_update_lead_status_still_encodes_the_value():
    """The URL-encoding fix is independent of all this and must survive."""
    src = inspect.getsource(supabase_client.update_lead_status)
    assert "urllib.parse.quote" in src
    assert "eq.{encoded_domain}" in src
