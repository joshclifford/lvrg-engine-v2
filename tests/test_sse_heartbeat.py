"""The build stream must not go silent while a slow step runs.

panchitasbakery.com died mid-build on 10 Sep: leadscraper logged "error reading
a body from connection" while the engine was two seconds into generation, and
the whole build was lost. Its intel step had run thirty-six seconds without
putting a single byte on the wire — scrape, Playwright re-render, Claude
extraction, photo download and press search all sit inside one await.

These pin the keepalive that fills those gaps, and pin the thing that matters
more: it must not take ownership of the task it is waiting on. _build_and_report
depends on precise cancellation semantics, and a wrapper that awaited or
cancelled the task would break the incident-hardening that function exists for.
"""

import asyncio

import api


def _run(coro):
    """Run an async scenario without pytest-asyncio (not installed here)."""
    return asyncio.run(coro)


def test_a_slow_step_gets_keepalives():
    async def scenario():
        task = asyncio.ensure_future(asyncio.sleep(0.25))
        beats = [b async for b in api._heartbeat_until(task, interval=0.05)]
        return beats

    beats = _run(scenario())

    assert len(beats) >= 3, beats
    assert all(b == ": keepalive\n\n" for b in beats)


def test_a_keepalive_is_an_sse_comment_no_client_has_to_understand():
    """Starts with ":", so the SSE spec says every client ignores it. A "data:"
    frame would reach leadscraper's reader as an event with no type and could be
    mistaken for a malformed build event."""
    assert api._SSE_KEEPALIVE.startswith(":")
    assert api._SSE_KEEPALIVE.endswith("\n\n")
    assert "data:" not in api._SSE_KEEPALIVE


def test_a_fast_step_gets_none():
    """A two-second intel step should look exactly as it did before."""
    async def scenario():
        task = asyncio.ensure_future(asyncio.sleep(0))
        return [b async for b in api._heartbeat_until(task, interval=5)]

    assert _run(scenario()) == []


def test_the_result_is_left_for_the_caller():
    """It waits on the task, it does not consume it. The caller still awaits the
    task itself and gets its value."""
    async def scenario():
        async def work():
            await asyncio.sleep(0.08)
            return "intel"

        task = asyncio.ensure_future(work())
        async for _ in api._heartbeat_until(task, interval=0.02):
            pass
        return await task

    assert _run(scenario()) == "intel"


def test_an_exception_still_reaches_the_caller():
    """A failing scrape must still raise where the pipeline can turn it into a
    reported failure. Swallowing it here would send the build down the "outcome
    unknown" path and leave the row `building` for the reaper — a real error
    quietly downgraded to an interruption."""
    async def scenario():
        async def work():
            await asyncio.sleep(0.04)
            raise RuntimeError("firecrawl exploded")

        task = asyncio.ensure_future(work())
        async for _ in api._heartbeat_until(task, interval=0.01):
            pass
        try:
            await task
        except RuntimeError as exc:
            return str(exc)
        return None

    assert _run(scenario()) == "firecrawl exploded"


def test_the_task_is_never_cancelled_by_the_wrapper():
    """_build_and_report survives a client disconnect precisely because nothing
    cancels the thread underneath it. A wrapper that cancelled on its way out
    would undo that."""
    async def scenario():
        task = asyncio.ensure_future(asyncio.sleep(0.06))
        async for _ in api._heartbeat_until(task, interval=0.01):
            pass
        return task.cancelled(), task.done()

    cancelled, done = _run(scenario())
    assert not cancelled
    assert done
