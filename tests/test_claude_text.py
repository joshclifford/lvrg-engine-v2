"""first_text must read the TEXT block, not block zero.

Regression cover for the 2 Sep 2026 outage: moving generation from
claude-opus-4-5 to claude-sonnet-5 turned adaptive thinking on, so every
response gained a leading ThinkingBlock and `response.content[0].text` raised
`'ThinkingBlock' object has no attribute 'text'` — every page of every build
failed, the leads were marked failed and the credits refunded.

The tests passed throughout, because the fakes emitted a bare text block with
no `type` and no thinking block in front of it. That is fixed in the generator
doubles too; this file pins the helper itself.
"""

from types import SimpleNamespace

from claude_text import first_text


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks))


THINKING = SimpleNamespace(type="thinking", thinking="let me plan this out")
TEXT = SimpleNamespace(type="text", text="<!DOCTYPE html>hello")


def test_reads_past_a_leading_thinking_block():
    """The exact shape that broke production."""
    assert first_text(_resp(THINKING, TEXT)) == "<!DOCTYPE html>hello"


def test_still_reads_a_text_only_response():
    """Haiku and the pre-4.6 models return this — must not regress."""
    assert first_text(_resp(TEXT)) == "<!DOCTYPE html>hello"


def test_skips_redacted_thinking_and_tool_use_too():
    """`type != "text"` is the rule, not "not thinking" — a response may carry
    redacted_thinking, tool_use or fallback blocks ahead of the text."""
    redacted = SimpleNamespace(type="redacted_thinking", data="...")
    tool = SimpleNamespace(type="tool_use", id="t1", name="x", input={})
    assert first_text(_resp(redacted, tool, TEXT)) == "<!DOCTYPE html>hello"


def test_thinking_only_reply_returns_the_default_rather_than_raising():
    """A model that burned its whole budget thinking produced no text. That is
    a truncation for the caller's `stop_reason` check to report, not an
    AttributeError three frames deep."""
    assert first_text(_resp(THINKING)) == ""
    assert first_text(_resp(THINKING), default="<!-- none -->") == "<!-- none -->"


def test_handles_blocks_replayed_from_json():
    """A cached fixture is dicts, not SDK objects."""
    resp = _resp({"type": "thinking", "thinking": "x"}, {"type": "text", "text": "hi"})
    assert first_text(resp) == "hi"


def test_empty_or_absent_content_is_the_default():
    assert first_text(_resp()) == ""
    assert first_text(SimpleNamespace(content=None)) == ""
    assert first_text(object()) == ""


def test_returns_the_first_text_block_when_there_are_several():
    second = SimpleNamespace(type="text", text="second")
    assert first_text(_resp(THINKING, TEXT, second)) == "<!DOCTYPE html>hello"
