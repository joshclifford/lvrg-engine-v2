"""The generated page is given a star rating and a count, and no review text at
all (see generator._build_reviews_block). So any sentence on a finished page
claiming to know what a reviewer SAID was invented by the model.

The prompt already said not to. It said so in capitals, in three places. The
live Pop Pie Co Sponsored Story published

    "Yelp reviewers have specifically called out the key lime pie after a
     recipe overhaul, calling it exactly what 'was needed'"

anyway (POD01-133). Two things were wrong. The rule named only a CUSTOMER, and
"Yelp reviewers" is not a customer, so the sentence broke nothing. And nothing
between the model and the prospect's inbox ever read the output.

_find_attributed_review_claims is the second half: the prompt asks, this checks.
Same posture as _count_own_domain_links, which exists because an earlier prompt
asked for backlinks and was ignored outright.
"""

import generator


# The exact sentence that shipped, on a real business's page, in a cold email to
# that business. If nothing else in this file passes, this must.
POP_PIE_LINE = (
    "Yelp reviewers have specifically called out the key lime pie after a "
    "recipe overhaul, calling it exactly what 'was needed'."
)


def test_catches_the_line_that_actually_shipped():
    hits = generator._find_attributed_review_claims(f"<p>{POP_PIE_LINE}</p>")
    assert len(hits) == 1
    assert "key lime pie" in hits[0]


def test_catches_a_named_platform_without_quotation_marks():
    """Deleting the quote marks does not make the claim true. The page still
    reports what reviewers said on a page holding no review text."""
    html = "<p>Yelp reviewers consistently praise the flaky crust here.</p>"
    assert generator._find_attributed_review_claims(html)


def test_catches_an_unnamed_group_in_quotation_marks():
    """"Regulars" and "diners" are not safer than a named person. They are the
    same fabricated evidence, only harder for anyone to check."""
    html = "<p>Regulars describe it as \"the best pie in San Diego\".</p>"
    assert generator._find_attributed_review_claims(html)


def test_catches_the_softer_reporting_verbs():
    for line in (
        "Reviewers often mention the patio.",
        "Reviews rave about the cold brew.",
        "Diners say the service is quick.",
    ):
        assert generator._find_attributed_review_claims(f"<p>{line}</p>"), line


def test_the_bare_rating_stat_is_not_a_hit():
    """The rating and count ARE real, passed in from Apify. Warning on them would
    fire on every page that has reviews at all, and a warning that always fires
    is one nobody reads."""
    html = "<p>Rated 4.7 stars from 1,714 reviews.</p><p>4.7 rating, 1,714 reviews</p>"
    assert generator._find_attributed_review_claims(html) == []


def test_ordinary_editorial_colour_is_not_a_hit():
    """"A spot locals love" is how every city guide on earth writes, and it
    claims no evidence. Flagging it would bury the real hits under noise."""
    html = (
        "<p>A neighborhood spot locals love.</p>"
        "<p>Visitors come for the pie and stay for the patio.</p>"
    )
    assert generator._find_attributed_review_claims(html) == []


def test_the_business_own_words_are_not_a_hit():
    """The Sponsored Story pull quote is the business's own copy set large. That
    is ordinary editorial and the prompt asks for it on purpose."""
    html = '<blockquote>"We bake everything the morning we sell it."</blockquote>'
    assert generator._find_attributed_review_claims(html) == []


def test_script_and_style_contents_are_ignored():
    """The chat widget and Tailwind config carry strings that are not page copy.
    A hit inside them would be unreadable and unactionable."""
    html = (
        "<script>var t = 'reviewers say \"great\"';</script>"
        "<style>/* reviewers praise this */</style>"
        "<p>Rated 4.7 stars from 1,714 reviews.</p>"
    )
    assert generator._find_attributed_review_claims(html) == []


def test_entity_encoded_quotes_still_count():
    """Claude emits &quot; and curly quotes as readily as plain ones. Matching
    only the ASCII double quote would miss most of them."""
    for quoted in (
        "Reviewers call it &quot;the best in town&quot;.",
        "Reviewers call it “the best in town”.",
    ):
        assert generator._find_attributed_review_claims(f"<p>{quoted}</p>"), quoted


# ── the three ways the first version of this detector was wrong ─────────────
# Each of these was found by running it over the pages in output/sites rather
# than over hand-written snippets, and each made it silently useless.

def test_the_word_preview_is_not_the_word_review():
    """"preview" contains "review", and the claim bar on EVERY page generated
    here reads "This is a preview of your ...". Without word boundaries this
    fires on every build, and a warning that always fires is not a warning."""
    html = (
        '<p>"This is a preview of your ThereSanDiego.com listing"</p>'
        "<p>Preview unavailable. Previews are rebuilt nightly.</p>"
    )
    assert generator._find_attributed_review_claims(html) == []


def test_an_unclosed_style_block_is_still_stripped():
    """A response that hits max_tokens is stapled shut by _close_truncated_html,
    which appends </body></html> and does NOT close a style block. Matching only
    on </style> stripped nothing, and every CSS class name reached the scan:
    8 to 13 "findings" per page on real output, all of them stylesheet."""
    html = (
        "<head><style>\n"
        ".testimonials { padding: 120px; }\n"
        ".testimonial-card::before { content: '\"'; }\n"
        ".testimonial-author { display: flex; }\n"
    )
    assert generator._find_attributed_review_claims(html) == []


def test_the_testimonial_card_shape_is_caught():
    """The shape the model actually builds, from a real page in output/sites.

    The quote and the name it is attributed to sit in SIBLING elements, so no
    single sentence contains both. A sentence-level scan misses it completely,
    which is why the byline pass exists.
    """
    html = (
        '<div class="t-card">'
        '<p>"Knowing that every sip supports their carbon-negative mission '
        'makes the cocktails taste even better. The tasting room is gorgeous too!"</p>'
        '<div class="author"><span>MR</span><div>Marcus R.</div>'
        "<div>San Diego, CA</div></div>"
        "</div>"
    )
    hits = generator._find_attributed_review_claims(html)
    assert hits, "the fabricated testimonial card was not caught"
    assert any("Marcus R." in h for h in hits)


def test_a_byline_survives_the_block_tag_rewrite():
    """Closing block tags become ". ", so a byline arrives as "Marcus R.." and a
    pattern requiring whitespace or a comma after the initial matches nothing.
    This is the exact regression: it missed all three cards on the real page."""
    assert generator._find_attributed_review_claims(
        '<p>"' + "x" * 45 + '"</p><div>Marcus R.</div>'
    )


def test_one_report_per_testimonials_section():
    """The real page reads "Testimonials / What Adventurers Say" and matched the
    heading pattern twice, three words apart, for what is one finding."""
    html = "<h2>Testimonials</h2><p>What Adventurers Say</p>"
    section_hits = [
        h for h in generator._find_attributed_review_claims(html)
        if h.startswith("[testimonials section]")
    ]
    assert len(section_hits) == 1


def test_a_page_with_no_reviews_section_at_all_stays_silent():
    """The shape of an ordinary clean build: real rating stat, real services,
    local voice, no invented evidence. Four of the five pages in output/sites
    look like this and must stay at zero."""
    html = (
        '<p>"This is a preview of your ThereSanDiego.com listing"</p>'
        "<h1>Dark Horse Coffee Roasters</h1>"
        "<p>Rated 4.7 stars from 1,714 reviews.</p>"
        "<p>A Normal Heights roaster locals have kept busy for a decade.</p>"
        "<p>70,000+ monthly visitors read ThereSanDiego.</p>"
        "<p>Cleanings. Whitening. Wholesale coffee subscriptions.</p>"
    )
    assert generator._find_attributed_review_claims(html) == []


def test_a_sanctioned_pull_quote_mentioning_locals_is_not_a_hit():
    """The Sponsored Story prompt ASKS for a pull quote drawn from the business's
    own copy, in a voice it calls "locals-know-locals". So a pull quote reading
    "...for the locals who kept asking" is quote marks plus a soft noun, and
    treating that as enough warned on output the prompt had just requested.

    That is the same defect as the preview/review one: a warning that fires on
    legitimate output is a warning nobody reads. Quote marks alone now only
    count beside a reviewer or a platform.
    """
    for line in (
        '"We built this place for the locals who kept asking us to open one."',
        '"Our guests come for the pie and stay for the patio."',
        '"Twenty years of visitors and we still bake it the same way."',
    ):
        assert generator._find_attributed_review_claims(f"<blockquote>{line}</blockquote>") == [], line


def test_the_split_keeps_every_real_catch():
    """The narrowing must not cost a single genuine finding: each of these
    carries either a reporting verb or a reviewer/platform noun."""
    for line in (
        POP_PIE_LINE,                                             # strict + reporting
        "Yelp reviewers consistently praise the flaky crust.",     # strict + reporting
        'Regulars describe it as "the best pie in San Diego".',    # soft + reporting
        "Diners say the service is quick.",                        # soft + reporting
        'Reviewers call it "the best in town".',                   # strict + quoted
    ):
        assert generator._find_attributed_review_claims(f"<p>{line}</p>"), line


# ── the offer pages, which nothing had ever been tested against ─────────────
# output/sites holds five pages and every one is a Smart Site build (claim bar
# reads "Claim This Site"). The two page types this whole check exists for had
# only ever been tested against snippets, and the shapes below are what turned
# up the moment a full one was written out to the prompt's own spec.

CLEAN_SPONSORED_STORY = """<!DOCTYPE html><html><body>
<div>"This is a preview of your Sponsored Story" <a href="#">Claim This Feature</a></div>
<h1>The Bakery That Made La Jolla Rethink Dessert</h1>
<p>By There San Diego Staff, San Diego, CA</p>
<p>Pop Pie Co has been baking savory pies in La Jolla since 2016.</p>
<h2>What the neighborhood knows</h2>
<p>Regulars line up before noon. The shop keeps hours that suit locals.</p>
<blockquote>We bake everything the morning we sell it, and we always have.</blockquote>
<p>Rated 4.7 stars from 1,714 reviews.</p>
<h2>Reach</h2>
<p>70,000+ monthly visitors. 700,000+ monthly reach. 25,000+ newsletter subscribers.
80,000+ social followers across Facebook and Instagram.</p>
</body></html>"""

CLEAN_GET_LISTED = """<!DOCTYPE html><html><body>
<div>"This is a preview of your ThereSanDiego.com listing" <a href="#">Claim This Listing</a></div>
<h1>Pop Pie Co</h1><p>Pop Pie Co, La Jolla, CA</p>
<h2>Why List Here</h2>
<p>This listing lives on a local guide 70,000+ San Diegans read every month, not on a
pay-to-play directory. Permanent page, no monthly fee. Live within 5 business days.</p>
<p>Rated 4.7 stars from 1,714 reviews.</p>
</body></html>"""


def test_a_clean_sponsored_story_is_silent():
    """Built to the prompt's spec: mandated byline and dateline, an unquoted
    pull quote, the real rating stat, the audience strip. Nothing invented."""
    assert generator._find_attributed_review_claims(CLEAN_SPONSORED_STORY, "Pop Pie Co") == []


def test_a_clean_get_listed_is_silent():
    """A directory profile is built around the business's address, and
    "Pop Pie Co, La Jolla, CA" has the same shape as a testimonial byline."""
    assert generator._find_attributed_review_claims(CLEAN_GET_LISTED, "Pop Pie Co") == []


def test_the_publications_own_byline_is_not_a_testimonial():
    """The Sponsored Story prompt REQUIRES byline "There San Diego Staff" and a
    dateline of "San Diego". Warning on it is warning on required content."""
    for byline in (
        "By There San Diego Staff, San Diego, CA",
        "There San Diego Staff, San Diego, CA",
    ):
        html = '<p>"' + "z" * 45 + f'"</p><p>{byline}</p>'
        assert generator._find_attributed_review_claims(html) == [], byline


def test_the_businesss_own_address_is_not_a_byline():
    """Knowing the business name is what separates a location from a person."""
    html = (
        "<blockquote>\"We bake everything the morning we sell it, every day.\"</blockquote>"
        "<p>Pop Pie Co, La Jolla, CA</p>"
    )
    assert generator._find_attributed_review_claims(html, "Pop Pie Co") == []


def test_the_claim_bar_is_never_a_candidate_quote():
    """The prompt writes the claim bar inside quotation marks and it clears the
    40-character floor, so it paired with any address later in the header."""
    for bar in (
        "This is a preview of your Sponsored Story",
        "This is a preview of your ThereSanDiego.com listing",
        "This is a preview of your new website for Pop Pie Co",
    ):
        assert len(bar) >= 40, bar
        html = f'<div>"{bar}"</div><p>Ana, San Diego, CA</p>'
        assert generator._find_attributed_review_claims(html) == [], bar


def test_a_business_named_after_a_place_does_not_hide_a_real_testimonial():
    """The business-name exclusion matches the START of the byline, not anywhere
    in it. San Diego place names are ordinary business names here, so a
    substring test meant a business called "Vista" or "Diego" silently swallowed
    "Marcus R., San Diego, CA" — a missed fabrication, which is the dangerous
    direction for this check to fail in."""
    card = '<p>"' + "x" * 45 + '"</p><div>Marcus R.</div><div>San Diego, CA</div>'
    for name in ("Diego", "Vista", "Carlsbad", "Pop Pie Co", ""):
        assert generator._find_attributed_review_claims(card, name), name


def test_a_person_vouching_still_fires_after_the_narrowing():
    """The exclusions must not cost the shape they were narrowed around."""
    for html in (
        '<p>"' + "x" * 45 + '"</p><div>Marcus R.</div><div>San Diego, CA</div>',
        '<p>"' + "y" * 45 + '"</p><div>Ana, San Diego, CA</div>',
    ):
        assert generator._find_attributed_review_claims(html, "Pop Pie Co"), html


def test_smart_site_is_checked_too(monkeypatch, tmp_path):
    """The fabricated testimonials that prompted all of this were found on a
    SMART SITE page in output/sites, not on a lead magnet. Checking only the two
    new magnets would have left the one path with a known real failure unwatched.
    """
    import sys

    sys.path.insert(0, "tests")
    from test_generate_page import _rich_intel, _nav_plan, _mock_client

    page = (
        "<!DOCTYPE html><html><body><h2>Testimonials</h2>"
        '<p>"' + "The private event space and custom cocktail menu were flawless. " * 2 + '"</p>'
        "<div>Jennifer T.</div><div>Carlsbad, CA</div></body></html>"
    )
    monkeypatch.setattr(generator, "_get_client", lambda **k: _mock_client([], html=page))
    monkeypatch.setattr(generator, "SITES_DIR", str(tmp_path))

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    generator.generate_site(_rich_intel(), "acme-com")

    assert any("reports what reviewers said" in line for line in printed)
    assert any("Jennifer T." in line for line in printed)


def test_the_rule_the_model_broke_is_in_the_prompt_text():
    """The detector is the backstop, not the fix. The prompt has to close the
    shape too, or every build warns and every build ships it anyway."""
    rule = generator.NO_REVIEW_TEXT_RULE
    assert "Yelp" in rule
    assert "reviewers" in rule
    # The paraphrase branch: this is the half the old wording never covered.
    assert "No unquoted paraphrase either" in rule
