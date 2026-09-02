"""AI spend accounting for a single build.

WHY: the engine has always thrown `response.usage` away. Anthropic returns the
token counts on every reply for free, and nothing read them — so nothing
downstream could answer "what did this lead magnet cost us?". leadscraper's
COGS dashboard has been showing a hardcoded 20¢/build placeholder instead
(build-smart-site/index.ts), which is roughly right for a single-page build and
about 4x low for a four-page one. This module turns the counts we already get
into a real number the callback can report.

PER-BUILD, NOT GLOBAL. Builds run concurrently behind a thread-pool executor
and generate_multi_page_site fans out further into PAGE_GENERATION_CONCURRENCY
threads, so a module-level accumulator would bill one prospect's pages to
another — the same class of bug pages.plan_pages returns fresh dicts to avoid.
Each pipeline run makes its own CostMeter and passes it down.

NEVER RAISES. This is an accounting side-car: a build must not fail because the
bookkeeping did. Every entry point swallows its own errors and logs.
"""

import threading

# USD per million tokens, from Anthropic's pricing page (checked 2 Sep 2026).
# (input, output). Cache rates are derived below rather than listed, since
# Anthropic prices them as fixed multiples of the input rate.
#
# Keys are the exact strings passed to `model=`. A model missing here still
# records its tokens but contributes $0 and prints a warning — a visibly-zero
# line is recoverable, a silently-wrong bill is not.
MODEL_PRICES_PER_MTOK = {
    "claude-sonnet-5":  (2.00, 10.00),
    "claude-opus-5":    (5.00, 25.00),
    "claude-opus-4-5":  (5.00, 25.00),
    "claude-haiku-4-5": (1.00,  5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}

# Anthropic's cache multipliers on the base INPUT rate. The engine does not use
# prompt caching today (every call carries a different prospect's content, and
# the fixed part of each prompt is below the minimum cacheable prefix), so these
# are always 0 in practice. Priced anyway so that turning caching on later does
# not silently start under-reporting.
CACHE_WRITE_MULTIPLIER = 1.25   # 5-minute write
CACHE_READ_MULTIPLIER = 0.10    # cache hit


def _usage_field(usage, name: str) -> int:
    """Read one token count off an SDK usage object.

    Tolerant on purpose: the field set grows between SDK versions (cache
    counters arrived after this engine's pinned 0.121.0), and a missing
    attribute must read as zero rather than blow up a build.
    """
    try:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        return int(value or 0)
    except Exception:
        return 0


def price_call_cents(model: str, usage) -> float:
    """Cost of one API call, in US cents. Unknown model -> 0.0."""
    prices = MODEL_PRICES_PER_MTOK.get(model)
    if not prices:
        print(f"  [cost] WARNING: no price for model {model!r} — counted as $0")
        return 0.0

    in_rate, out_rate = prices
    dollars = (
        _usage_field(usage, "input_tokens") * in_rate
        + _usage_field(usage, "output_tokens") * out_rate
        + _usage_field(usage, "cache_creation_input_tokens") * in_rate * CACHE_WRITE_MULTIPLIER
        + _usage_field(usage, "cache_read_input_tokens") * in_rate * CACHE_READ_MULTIPLIER
    ) / 1_000_000
    return dollars * 100


class CostMeter:
    """Accumulates what one build spent across its 3-6 Claude calls.

    Thread-safe: generate_multi_page_site records from several worker threads
    at once.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = []

    def record(self, step: str, model: str, usage) -> None:
        """Log one call. Best-effort — never raises into the caller."""
        try:
            entry = {
                "step": step,
                "model": model,
                "input_tokens": _usage_field(usage, "input_tokens"),
                "output_tokens": _usage_field(usage, "output_tokens"),
                "cents": price_call_cents(model, usage),
            }
            with self._lock:
                self._calls.append(entry)
        except Exception as e:
            print(f"  [cost] failed to record {step} on {model}: {e}")

    @property
    def total_cents(self) -> float:
        with self._lock:
            return sum(c["cents"] for c in self._calls)

    def summary(self) -> dict:
        """What the callback reports and the log line prints."""
        with self._lock:
            calls = list(self._calls)
        return {
            "total_cents": round(sum(c["cents"] for c in calls), 4),
            "calls": len(calls),
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "by_step": [
                {
                    "step": c["step"],
                    "model": c["model"],
                    "input_tokens": c["input_tokens"],
                    "output_tokens": c["output_tokens"],
                    "cents": round(c["cents"], 4),
                }
                for c in calls
            ],
        }


def record(meter, step: str, model: str, response) -> None:
    """Record `response`'s usage against `meter`, if there is one.

    A module-level helper so every call site is one line and a None meter —
    lm-tool, the CLI, the smoke test, every existing test — is a no-op rather
    than a branch at each call.
    """
    if meter is None:
        return
    try:
        meter.record(step, model, getattr(response, "usage", None))
    except Exception as e:
        print(f"  [cost] record failed for {step}: {e}")
