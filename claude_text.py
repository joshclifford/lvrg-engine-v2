"""Read the text out of a Claude response.

WHY THIS EXISTS (2 Sep 2026): every call site used to do
`response.content[0].text`, which quietly assumes the FIRST block of the
response is the text. That held on claude-opus-4-5, which does not think
unless asked. It stopped holding the moment generation moved to
claude-sonnet-5, where adaptive thinking is ON BY DEFAULT and the reply comes
back as two blocks:

    content[0] = ThinkingBlock   <- the reasoning
    content[1] = TextBlock       <- the HTML we actually wanted

so `content[0].text` raised `'ThinkingBlock' object has no attribute 'text'`
and every page of every build failed. The intel step survived only because it
is still on claude-haiku-4-5, which does not auto-think — a coincidence, not a
design.

Index 0 was never the contract. The API's contract is "the content list holds
blocks; find the type you want", and a response may also carry
`redacted_thinking`, tool-use or (on newer models) `fallback` blocks ahead of
the text. Reaching for a type instead of a position makes the model choice a
model choice again, rather than something that silently changes how a response
parses.
"""


def first_text(response, default: str = "") -> str:
    """The first text block's content, or `default` if the reply has none.

    Tolerant of block objects and of plain dicts, because a response replayed
    from JSON (tests, cached fixtures) is not made of SDK objects.

    Returns `default` rather than raising on a thinking-only reply: a model
    that spent its whole budget thinking and produced no text is a truncation
    to handle upstream (`stop_reason == "max_tokens"` already warns), not an
    AttributeError three frames deep.
    """
    for block in getattr(response, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type is None and isinstance(block, dict):
            block_type = block.get("type")
        if block_type != "text":
            continue
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text is not None:
            return text
    return default
