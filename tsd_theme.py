"""The ThereSanDiego.com article chrome, rebuilt as standalone HTML.

A Sponsored Story is sold as an article that RUNS on ThereSanDiego.com, so the
mockup only does its job if it looks like one. It did not. The model was asked
for a whole page and wrote a Tailwind landing page: centred headline, no site
header, no sidebar, no footer, fonts of its own choosing. Set next to the live
page at theresandiego.com/san-diego-realtors/josh-taylor-neighborhood/ it was
plainly a different website, which is the one thing a "here is your story on
our site" mockup cannot be.

So the chrome stops being generated. Everything here is fixed markup measured
off that live page: the header and its menu, the sponsored-listing disclosure,
the 8/4 column split, the three sidebar widgets, the footer. The model writes
the ARTICLE ONLY and generator.py drops it into the left column.

Every size, weight and colour below was READ OFF the live page with
getComputedStyle, not taken from a screenshot and not from the theme's
customizer block. Those two disagree: the customizer writes `h1 {font-size:
3.23rem}`, which looks like 52px until you notice the theme sets the root font
to 13px, and the real headline is 42px. Anything sized by eye here would be
wrong by a quarter.

    h1  Oswald 600 42/46 #4a4a4a        body/article  Roboto 18/1.7 #333
    h2  Oswald 400 32/35 #4a4a4a        article links #da195b
    nav Poppins 500 15 #222 upper       widget titles Roboto 600 16 #333
    sidebar post titles Oswald 400 18 #222 upper, fact rows 14 #888

The stylesheet is hand-written rather than linked, because the live page pulls
twenty-odd WordPress stylesheets and a preview has to render standalone from a
static host with no theme behind it.

Get Listed uses the same chrome around a Business Profile instead of an
article (render_profile_page, POD01-251), as the live profiles do.

"What's Hot" and "Upcoming Events" are real TSD posts and events with their
real thumbnails, hardcoded on purpose. They are the same for every prospect:
their job is to make the page read as a live magazine, and a model asked to
invent local events would invent local events.
"""

import re
from html import escape
from urllib.parse import quote_plus, urlparse

TSD_HOME = "https://theresandiego.com"
TSD_LOGO = f"{TSD_HOME}/wp-content/uploads/mobile_logo-1.png"
TSD_FOOTER_LOGO = f"{TSD_HOME}/wp-content/uploads/dark-logo.png"
TSD_FOOTER_BG = f"{TSD_HOME}/wp-content/uploads/tsd-footer.png"

# The disclosure the live page carries above every sponsored profile, verbatim.
# It is what makes the placement disclosed as well as honest, so it is copied
# rather than paraphrased.
DISCLOSURE = (
    "<strong>Sponsored Listing:</strong> This page features a local business that has "
    "partnered with us to expand its reach in the San Diego community. We only feature "
    "partners we believe our readers would genuinely find valuable."
)
# The same disclosure worded for an article. The box above is the one TSD puts on
# its Get Listed profiles; a story is not a listing (POD01-264). Wording pending
# Josh's confirmation of what TSD prints on a paid story.
STORY_DISCLOSURE = (
    "<strong>Sponsored Story:</strong> This article features a local business that has "
    "partnered with us to expand its reach in the San Diego community. We only feature "
    "partners we believe our readers would genuinely find valuable."
)

NAV = [
    ("Eat + Drink", f"{TSD_HOME}/eat-drink/"),
    ("See + Do", f"{TSD_HOME}/see-do-events/"),
    ("Guides", f"{TSD_HOME}/guides/"),
    ("Free Stuff", f"{TSD_HOME}/giveaway/"),
    ("Advertise", "https://advertise.theresandiego.com/"),
]

# Real posts, real thumbnails, read off the live sidebar.
WHATS_HOT = [
    ("19 Amazing Road Trips From San Diego",
     f"{TSD_HOME}/road-trips-from-san-diego/",
     f"{TSD_HOME}/wp-content/uploads/Screenshot-2025-04-23-083904-150x150.png"),
    ("8 Places to Find the Best Bagels in San Diego",
     f"{TSD_HOME}/8-places-to-find-the-best-bagels-in-san-diego/",
     f"{TSD_HOME}/wp-content/uploads/6-Best-Bagel-Spots-In-San-Diego-150x150.png"),
    ("11 of the Best Hikes in San Diego...Most Within 15 Minutes of Your Door!",
     f"{TSD_HOME}/11-cant-miss-san-diego-hikes-most-within-15-minutes-of-your-door/",
     f"{TSD_HOME}/wp-content/uploads/san-diego-hikes-150x150.png"),
    ("13 Must-Try San Diego Wineries (Who Needs Napa?!)",
     f"{TSD_HOME}/the-best-san-diego-wineries/",
     f"{TSD_HOME}/wp-content/uploads/Orfila-Vineyards-4-150x150.jpg"),
    ("The Best San Diego Breweries to Enjoy a Cold One With Friends",
     f"{TSD_HOME}/best-san-diego-breweries/",
     f"{TSD_HOME}/wp-content/uploads/Viewpoint-150x150.jpg"),
]

# (title, url, image, date, time, venue, neighborhood)
UPCOMING_EVENTS = [
    ("Dive into the World of Tango at The Conrad",
     f"{TSD_HOME}/event/the-art-of-tango-the-conrad-music-and-movement-unite/",
     f"{TSD_HOME}/wp-content/uploads/Art-of-Tango-952x579.png",
     "February 27, 2027", "7:30 pm",
     "The Conrad Prebys Performing Arts Center", "La Jolla"),
    ("Sing Along to Every Hit at MANIA: The World's Number One ABBA Tribute Show",
     f"{TSD_HOME}/event/mania-abba-tribute-tour-you-cannot-miss/",
     f"{TSD_HOME}/wp-content/uploads/MANIA-952x579.jpeg",
     "February 10, 2027", "7:30 pm",
     "The Magnolia", "El Cajon"),
    ("Hear a World Premiere at Camarada's Where Beauty Persists",
     f"{TSD_HOME}/event/where-beauty-persists-the-conrad/",
     f"{TSD_HOME}/wp-content/uploads/Where-Beauty-Persists-952x579.jpg",
     "January 23, 2027", "7:30 pm",
     "The Conrad Prebys Performing Arts Center", "La Jolla"),
    ("Sing Along to Charlie Brown Jingles & Jazz at The Conrad",
     f"{TSD_HOME}/event/charlie-brown-jingles-and-jazz-the-conrad/",
     f"{TSD_HOME}/wp-content/uploads/Charlie-Brown-Jingles-Jazz-1-952x579.jpg",
     "December 19, 2026", "7:30 pm",
     "The Conrad Prebys Performing Arts Center", "La Jolla"),
    ("Fly Into the Holidays With A Magical Cirque Christmas in Oceanside",
     f"{TSD_HOME}/event/magical-cirque-christmas/",
     f"{TSD_HOME}/wp-content/uploads/Magical-Cirque-Christmas-2-952x579.jpg",
     "December 13, 2026", "5:30 pm",
     "", "Oceanside"),
]

FOOTER_LINKS = [
    ("Site Content", [
        ("Advertise With Us", "https://advertise.theresandiego.com/"),
        ("Send Us A Tip", f"{TSD_HOME}/contact-us/"),
        ("About Us", f"{TSD_HOME}/about/"),
        ("Work With Us", f"{TSD_HOME}/there-san-diego-jobs/"),
        ("Contact Us", f"{TSD_HOME}/contact-us/"),
    ]),
    ("More", [
        ("Contest Rules", f"{TSD_HOME}/contest-sweepstakes-rules/"),
        ("Privacy Policy", f"{TSD_HOME}/privacy/"),
        ("Terms Of Use", f"{TSD_HOME}/terms/"),
        ("DMCA Notice", f"{TSD_HOME}/dmca/"),
        ("Advertising Agreement", f"{TSD_HOME}/advertisingterms/"),
    ]),
]

FOOTER_ADDRESS = "3200 Paseo Village Way, #3140 San Diego CA 92130"
FOOTER_EMAIL = "sales@theresandiego.com"
FOOTER_PHONE = "(619) 350-4686"
FOOTER_SOCIALS = [
    ("Facebook", "https://www.facebook.com/theresandiego"),
    ("X", "https://twitter.com/ThereSanDiego"),
    ("Instagram", "https://www.instagram.com/theresandiego/"),
]

# Which TSD directory a business would be filed under. Only the ones the site
# actually publishes: an invented directory name is a wrong fact in the one
# block on the page a prospect reads as data rather than prose.
DIRECTORIES = {
    "realtor": ("San Diego Realtors", f"{TSD_HOME}/san-diego-realtors/"),
    # The Directory row on the live Elements Design & Build profile, 30 Sep 2026.
    "contractor": ("San Diego Contractors", f"{TSD_HOME}/san-diego-contractors/"),
    # The Directory row on the live Iron Orr Fitness profile, 1 Oct 2026.
    "trainer": ("San Diego Personal Trainers", f"{TSD_HOME}/san-diego-personal-trainers/"),
    "restaurant": ("Eat + Drink", f"{TSD_HOME}/eat-drink/"),
    "cafe": ("Eat + Drink", f"{TSD_HOME}/eat-drink/"),
    "bar": ("Eat + Drink", f"{TSD_HOME}/eat-drink/"),
    "bakery": ("Eat + Drink", f"{TSD_HOME}/eat-drink/"),
    "brewery": ("Eat + Drink", f"{TSD_HOME}/eat-drink/"),
}
DEFAULT_DIRECTORY = ("San Diego Guides", f"{TSD_HOME}/guides/")

# The three Sponsored Story tiers and the audience figures, straight off
# advertise.theresandiego.com. They used to be sentences in the prompt and came
# back as whatever the model felt like rendering. Fixed markup cannot drift,
# and the test that pins these numbers now reads the finished page instead of
# the instructions that asked for it.
#
# The cards themselves, description, features and both buttons, are word for
# word off advertise.theresandiego.com/story-plans, 30 Sep 2026, with a comma
# where the live copy has an em dash. "Get Started" opens that plan's own TSD
# checkout; "Speak with a Rep" opens our booking page, which is what the live
# button's Let's Chat pop-up books.
# (name, price, description, features, popular, checkout)
PLANS = [
    ("LOCAL", "$497",
     "Your neighbors are your customers. This plan targets San Diegans within a few miles of your "
     "door, the people most likely to walk in, come back, and tell their friends. Best for "
     "neighborhood restaurants, coffee shops, bars, salons, and retail that thrive on local foot "
     "traffic and repeat visits.",
     ["1 per quarter sponsored story", "10,000/month guaranteed impressions"],
     False, "https://advertise.theresandiego.com/sponsoredstories-local-checkout"),
    ("CITYWIDE", "$997",
     "Some businesses draw from all over San Diego, not just the block. This plan promotes your "
     "story across the full metro area, reaching San Diegans who drive 20+ minutes for the right "
     "experience. Best for destination restaurants, theater and live events, fitness studios with "
     "a citywide following, and any business where 'worth the trip' is the selling point.",
     ["1 per month sponsored story", "25,000/month guaranteed impressions"],
     True, "https://advertise.theresandiego.com/sponsoredstories-citywide-checkout"),
    ("COUNTYWIDE", "$1,500",
     "Maximum reach across all of San Diego County, from Oceanside to the border. Best for "
     "businesses and events that want to be seen everywhere, by everyone. Ideal for major events, "
     "multi-location businesses, and brands building countywide awareness.",
     ["1 per month sponsored story", "50,000/month guaranteed impressions"],
     False, "https://advertise.theresandiego.com/sponsoredstories-countywide-checkout"),
]
# The same three lines close every card on the live page.
PLAN_COMMON_FEATURES = ["Targeting: Geo, age &amp; demo", "Ad campaign management included",
                        "Organic impressions not charged"]

# TSD's own published figures, including the "+". 80,000+ is a floor the client
# publishes; 82,000 was somebody adding 40k Facebook to 42k Instagram and
# printing the sum as an exact count (POD01-129).
REACH = [
    ("70,000+", "Monthly Visitors"),
    ("700,000+", "Monthly Reach"),
    ("25,000+", "Newsletter Subscribers"),
    ("80,000+", "Social Followers (FB &amp; IG)"),
]
GUARANTEE = ("Every Sponsored Story comes with guaranteed impressions. "
             "If we don't hit the number, we keep promoting until we do.")
PLANS_FOOTNOTE = ("Every plan includes ad campaign management and geo, age and demographic "
                  "targeting. Organic impressions are never charged.")

# The three Get Listed tiers, word for word off advertise.theresandiego.com/
# get-listed, with the page each button opens there, 30 Sep 2026. Ranges use a
# hyphen where the live page has an en dash, like every other dash we publish.
#
# All three by Fazal's call, 30 Sep 2026. The client's campaign-get-listed.md
# says "Sell Tier 1 today ... Don't quote or collect for those [Tier 2 and 3] on
# a cold sequence"; the preview now shows what the live page shows instead.
ADVERTISE_HOME = "https://advertise.theresandiego.com"
# (tier, name, description, note, price, per, button, url, recommended)
GET_LISTED_TIERS = [
    ("TIER 1", "Base Profile",
     "Permanent business profile page on ThereSanDiego.com with cross-referenced business + "
     "owner schema markup, photo gallery, and category directory listing.",
     "", "$297", "", "Get Started", f"{ADVERTISE_HOME}/get-listed-tier-1-checkout", False),
    ("TIER 2", "Keyword Claims",
     "Everything in Tier 1, plus 3-5 search phrases that are yours alone. When we write a guide "
     "or a neighborhood piece that touches your specialty, those phrases link back to your profile.",
     "<em>Yours for <strong>12 months</strong>.</em>", "$597", "", "Get Started",
     f"{ADVERTISE_HOME}/get-listed-tier-2-checkout", True),
    ("TIER 3", "Pillar Guide Placement",
     "Everything in Tiers 1 and 2, plus featured placement in the ThereSanDiego pillar guide for "
     "the customer's category.",
     "Capacity-capped 1-2 slots per neighborhood.", "$997", "/ year", "Check Availability",
     f"{ADVERTISE_HOME}/scheduling-page", False),
]

_ICONS = {
    "directory": '<path d="M3 21V7l7-4v4l7-3v17h-5v-5h-4v5H3zm2-2h3v-3H5v3zm0-5h3v-3H5v3zm0-5h3V6L5 7.5V9zm5 10h3v-3h-3v3zm0-5h3v-3h-3v3zm0-5h3V6.5l-3 1.2V9zm5 10h3v-3h-3v3zm0-5h3v-3h-3v3z"/>',
    "pin": '<path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/>',
    "address": '<path d="M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm-9 12H5v-1.5c0-1.4 2.7-2 3-2s3 .6 3 2V16zm-3-4.5A1.75 1.75 0 1 1 9.75 9.8 1.75 1.75 0 0 1 8 11.5zM19 15h-6v-1.5h6V15zm0-3h-6v-1.5h6V12zm0-3h-6V7.5h6V9z"/>',
    "phone": '<path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1 11.4 11.4 0 0 0 .57 3.6 1 1 0 0 1-.25 1l-2.2 2.2z"/>',
    "website": '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm6.9 6h-2.95a15.6 15.6 0 0 0-1.4-3.6A8 8 0 0 1 18.9 8zM12 4.04c.83 1.2 1.48 2.53 1.9 3.96h-3.8c.42-1.43 1.07-2.76 1.9-3.96zM4.26 14A8 8 0 0 1 4 12c0-.69.1-1.36.26-2h3.38a16.5 16.5 0 0 0 0 4H4.26zm.84 2h2.95c.35 1.26.82 2.47 1.4 3.6A8 8 0 0 1 5.1 16zm2.95-8H5.1a8 8 0 0 1 4.35-3.6A15.6 15.6 0 0 0 8.05 8zM12 19.96A13.6 13.6 0 0 1 10.1 16h3.8A13.6 13.6 0 0 1 12 19.96zM14.34 14H9.66a14.7 14.7 0 0 1 0-4h4.68a14.7 14.7 0 0 1 0 4zm.2 5.6c.58-1.13 1.05-2.34 1.4-3.6h2.95a8 8 0 0 1-4.35 3.6zM16.36 14a16.5 16.5 0 0 0 0-4h3.38c.17.64.26 1.31.26 2s-.1 1.36-.26 2h-3.38z"/>',
    "social": '<path d="M18 16.1a2.9 2.9 0 0 0-1.95.77L8.9 12.7a3.3 3.3 0 0 0 0-1.4l7.05-4.11A2.98 2.98 0 1 0 15 5c0 .24.03.47.08.7L8.03 9.81a3 3 0 1 0 0 4.38l7.12 4.16c-.05.2-.08.41-.08.62a2.93 2.93 0 1 0 2.93-2.87z"/>',
    "clock": '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.6-3.9 2.3-.8-1.3 3.2-1.9V6h1.5v6.6z"/>',
    "calendar": '<path d="M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 16H5V10h14v10z"/>',
    "search": '<path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"/>',
    "instagram": '<path d="M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.7 3.7 0 0 1-1.38-.9 3.7 3.7 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16zM12 6.35a5.65 5.65 0 1 0 0 11.3 5.65 5.65 0 0 0 0-11.3zm0 9.32a3.67 3.67 0 1 1 0-7.34 3.67 3.67 0 0 1 0 7.34zm7.19-9.54a1.32 1.32 0 1 1-2.64 0 1.32 1.32 0 0 1 2.64 0z"/>',
    "facebook": '<path d="M22 12a10 10 0 1 0-11.56 9.88v-6.99H7.9V12h2.54V9.8c0-2.5 1.49-3.89 3.77-3.89 1.1 0 2.24.2 2.24.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56V12h2.78l-.45 2.89h-2.33v6.99A10 10 0 0 0 22 12z"/>',
    "linkedin": '<path d="M6.94 5a1.94 1.94 0 1 1-3.88 0 1.94 1.94 0 0 1 3.88 0zM3.11 8.4h3.66V21H3.11V8.4zm5.85 0h3.5v1.72h.05c.49-.92 1.68-1.9 3.45-1.9 3.69 0 4.37 2.43 4.37 5.59V21h-3.65v-5.48c0-1.31-.02-3-1.82-3-1.83 0-2.11 1.43-2.11 2.9V21H8.96V8.4z"/>',
    "tiktok": '<path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.09v12.4a2.59 2.59 0 0 1-2.59 2.5 2.59 2.59 0 1 1 .77-5.06V9.69a5.67 5.67 0 0 0-.77-.05A5.68 5.68 0 1 0 15.54 15.4V9.01a7.35 7.35 0 0 0 4.3 1.38V7.3a4.3 4.3 0 0 1-3.24-1.48z"/>',
    "youtube": '<path d="M21.58 7.19a2.51 2.51 0 0 0-1.77-1.78C18.25 5 12 5 12 5s-6.25 0-7.81.41a2.51 2.51 0 0 0-1.77 1.78A26.2 26.2 0 0 0 2 12a26.2 26.2 0 0 0 .42 4.81 2.51 2.51 0 0 0 1.77 1.78C5.75 19 12 19 12 19s6.25 0 7.81-.41a2.51 2.51 0 0 0 1.77-1.78A26.2 26.2 0 0 0 22 12a26.2 26.2 0 0 0-.42-4.81zM10 15.02V8.98L15.2 12 10 15.02z"/>',
}

# Each platform's own embed script, loaded once per page for the platforms
# whose posts it carries. Instagram's is the one the live article loads.
_EMBED_SCRIPTS = {
    "instagram": '<script async src="https://www.instagram.com/embed.js"></script>',
    "facebook": ('<div id="fb-root"></div>\n<script async defer crossorigin="anonymous" '
                 'src="https://connect.facebook.net/en_US/sdk.js#xfbml=1&amp;version=v23.0"></script>'),
    "tiktok": '<script async src="https://www.tiktok.com/embed.js"></script>',
}


def _icon(name: str, size: int = 24) -> str:
    return (f'<svg class="tsd-svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'aria-hidden="true">{_ICONS[name]}</svg>')


STYLES = """
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:#fff;color:#333;font-family:'Roboto',Arial,sans-serif;font-size:18px;line-height:1.7}
img{max-width:100%;height:auto}
a{color:#da195b}
h1,h2,h3,h4,h5,h6{font-family:'Oswald',Arial,sans-serif;font-weight:400;color:#4a4a4a;margin:0}
.tsd-container{max-width:1170px;margin:0 auto;padding:0 15px}
.tsd-row{display:flex;flex-wrap:wrap;margin:0 -15px}
.tsd-main{flex:0 0 66.66%;max-width:66.66%;padding:0 15px}
.tsd-side{flex:0 0 33.33%;max-width:33.33%;padding:0 15px}

.tsd-claim{position:sticky;top:0;z-index:99;background:#1a1a1a;color:#fff;padding:12px 15px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:center;text-align:center;font-size:15px}
.tsd-claim a{background:#f2c14e;color:#1a1a1a;font-family:'Poppins',Arial,sans-serif;font-weight:600;font-size:13px;letter-spacing:.03em;text-decoration:none;padding:7px 18px;border-radius:40px;white-space:nowrap}
.tsd-claim a:hover{background:#ffd166}

.tsd-header{border-bottom:1px solid #ececec}
.tsd-header .tsd-container{display:flex;align-items:center;gap:30px;padding-top:16px;padding-bottom:16px}
.tsd-logo img{height:58px;width:auto;display:block}
.tsd-nav{margin-left:auto}
.tsd-nav ul{display:flex;align-items:center;gap:26px;margin:0;padding:0;list-style:none}
.tsd-nav a{font-family:'Poppins',Arial,sans-serif;font-size:15px;font-weight:500;line-height:25.5px;text-transform:uppercase;color:#222;text-decoration:none}
.tsd-nav a:hover{color:#da195b}
.tsd-search{display:flex;align-items:center;gap:8px;border-bottom:1px solid #d5d5d5;padding-bottom:3px}
.tsd-search em{font-style:normal;font-family:'Roboto',Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#9b9b9b}
.tsd-search svg{fill:#222;flex:0 0 auto}

.tsd-disclosure{background:#fdf6ec;border:1px solid #f0e2c8;border-radius:6px;padding:14px 18px;margin:34px 0;font-size:14px;line-height:22.4px;color:#3c3427;text-align:center}

.tsd-article h1{font-size:42px;font-weight:600;line-height:46px;margin:0 0 18px}
.tsd-byline{font-size:14px;color:#888;padding-bottom:22px;border-bottom:1px solid #e4e4e4;margin-bottom:30px}
.tsd-byline strong{font-weight:500;color:#333}
.tsd-article h2{font-size:32px;line-height:35px;margin:42px 0 18px}
.tsd-article h3{font-size:24px;line-height:28px;margin:32px 0 14px}
.tsd-article p{font-size:18px;line-height:30.6px;color:#333;margin:0 0 22px}
.tsd-article a{color:#da195b;text-decoration:underline}
.tsd-article ul,.tsd-article ol{margin:0 0 24px;padding-left:20px}
.tsd-article li{font-size:18px;line-height:30.6px;color:#333;margin-bottom:11px}
.tsd-article img{display:block;width:100%;margin:0}
.tsd-hero{margin:0 0 30px}
.tsd-article figure{margin:32px 0}
.tsd-article figcaption{font-size:14px;color:#8a8a8a;padding-top:9px;line-height:1.5}
.tsd-pullquote{border-left:4px solid #da195b;padding:4px 0 4px 26px;margin:38px 0}
.tsd-pullquote p{font-family:'Oswald',Arial,sans-serif;font-size:28px;font-weight:300;line-height:1.35;color:#4a4a4a;margin:0}
.tsd-factbox{background:#f7f7f7;border:1px solid #ececec;padding:26px 28px;margin:40px 0}
.tsd-factbox h3{font-size:22px;line-height:28px;margin:0 0 14px}
.tsd-factbox ul{list-style:none;margin:0;padding:0}
.tsd-factbox li{font-size:15px;line-height:25.5px;color:#333;margin-bottom:9px}
.tsd-factbox li strong{font-family:'Oswald',Arial,sans-serif;font-weight:500;color:#4a4a4a}
.tsd-article blockquote{border:0;padding:0;margin:0 8% 28px;text-align:center}
.tsd-article blockquote::before{content:'\\201C';display:block;font-size:78px;line-height:78px;height:30px;overflow:hidden;margin-bottom:21px}
.tsd-gallery{display:flex;flex-wrap:wrap;gap:16px;margin:32px 0}
.tsd-article .tsd-gallery figure{flex:1 1 calc(50% - 8px);margin:0}
.tsd-article .tsd-gallery img{aspect-ratio:3/2;object-fit:cover}

.tsd-embed{border:1px solid #dbdbdb;border-radius:3px;margin:34px 0;padding:14px 16px;display:flex;align-items:center;gap:12px;background:#fff}
.tsd-embed-avatar{flex:0 0 40px;width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:linear-gradient(45deg,#f9ce34,#ee2a7b,#6228d7)}
.tsd-embed-avatar svg{fill:#fff}
.tsd-embed-who{min-width:0;flex:1 1 auto}
.tsd-embed-handle{font-family:'Roboto',Arial,sans-serif;font-size:14px;font-weight:600;color:#262626;line-height:1.3;overflow-wrap:anywhere}
.tsd-embed-where{font-family:'Roboto',Arial,sans-serif;font-size:12px;font-weight:400;color:#8e8e8e;line-height:1.4}
.tsd-embed a.tsd-embed-btn{flex:0 0 auto;background:#0095f6;color:#fff;font-family:'Roboto',Arial,sans-serif;font-size:13px;font-weight:600;text-decoration:none;padding:7px 14px;border-radius:8px;white-space:nowrap}
.tsd-embed a.tsd-embed-btn:hover{background:#1877f2}

.tsd-article .tsd-post{margin:34px auto;max-width:540px}
.tsd-article .tsd-post-video{max-width:none;position:relative;aspect-ratio:16/9}
.tsd-post-video iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:0}
.tsd-sitepost{border:1px solid #dbdbdb;border-radius:3px;background:#fff;overflow:hidden}
.tsd-sitepost .tsd-embed{border:0;border-radius:0;margin:0}
.tsd-sitepost .tsd-embed-avatar{background:#da195b}
.tsd-article .tsd-sitepost img{max-height:600px;object-fit:cover}
.tsd-article .tsd-sitepost a.tsd-sitepost-more{display:block;border-top:1px solid #efefef;padding:12px 16px;font-family:'Roboto',Arial,sans-serif;font-size:13px;color:#00376b;text-decoration:none}

.tsd-widget{margin-bottom:44px}
.tsd-widget-title{border-bottom:1px solid #e4e4e4;padding-bottom:11px;margin-bottom:20px}
.tsd-widget-title span{font-family:'Roboto',Arial,sans-serif;font-size:16px;font-weight:600;line-height:27px;color:#333}
.tsd-details{list-style:none;margin:0;padding:0}
.tsd-details li{display:flex;gap:14px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #f1f1f1}
.tsd-details li:last-child{border-bottom:0}
.tsd-details svg{fill:#da195b;flex:0 0 auto;margin-top:2px}
.tsd-dt{font-family:'Oswald',Arial,sans-serif;font-size:12px;font-weight:400;letter-spacing:.04em;text-transform:uppercase;color:#333;line-height:15.6px;margin-bottom:4px}
.tsd-dd{font-size:14px;line-height:23.8px;color:#888;word-break:break-word}
.tsd-dd a{color:#888;text-decoration:none}
.tsd-dd a:hover{color:#da195b}

.tsd-social{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.tsd-social a.tsd-social-icon{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:#da195b;text-decoration:none}
.tsd-social a.tsd-social-icon:hover{background:#b3124a}
.tsd-social a.tsd-social-icon svg{fill:#fff;margin:0}
.tsd-factbox li.tsd-follow{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:14px 0 0}
.tsd-follow-strip{display:flex;flex-wrap:wrap;align-items:center;gap:12px;border-top:1px solid #e4e4e4;padding-top:20px;margin:36px 0 0}
.tsd-follow-strip strong{font-family:'Oswald',Arial,sans-serif;font-size:18px;font-weight:500;color:#4a4a4a}

.tsd-hot{display:flex;gap:14px;align-items:center;margin-bottom:18px}
.tsd-hot img{flex:0 0 78px;width:78px;height:60px;object-fit:cover}
.tsd-hot a{font-family:'Oswald',Arial,sans-serif;font-size:18px;font-weight:400;line-height:23.4px;text-transform:uppercase;color:#222;text-decoration:none}
.tsd-hot a:hover{color:#da195b}

.tsd-event{margin-bottom:30px}
.tsd-event img{width:100%;height:165px;object-fit:cover;display:block}
.tsd-event-title{display:block;font-family:'Oswald',Arial,sans-serif;font-size:19px;font-weight:400;line-height:26.6px;color:#222;text-decoration:none;margin:14px 0 10px}
.tsd-event-title:hover{color:#da195b}
.tsd-meta{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;line-height:12px;color:#777;align-items:center}
.tsd-meta span{display:inline-flex;align-items:center;gap:5px}
.tsd-meta svg{fill:#da195b}

.tsd-guarantee{background:#f2f2f2;padding:46px 15px;margin-top:70px;text-align:center}
.tsd-guarantee p{font-family:'Oswald',Arial,sans-serif;font-size:1.5rem;font-weight:300;line-height:1.45;max-width:760px;margin:0 auto;color:#1a1a1a}
.tsd-reach{padding:56px 15px;text-align:center}
.tsd-reach h2{font-size:2.1rem;font-weight:500;margin-bottom:34px}
.tsd-reach-grid{display:flex;flex-wrap:wrap;gap:18px;justify-content:center}
.tsd-stat{flex:1 1 200px;max-width:250px;border:1px solid #ececec;padding:26px 14px}
.tsd-stat b{display:block;font-family:'Oswald',Arial,sans-serif;font-size:2.1rem;font-weight:600;color:#da195b;line-height:1.1}
.tsd-stat span{display:block;font-size:13px;color:#6b6b6b;margin-top:7px}
.tsd-plans{background:#141414;color:#fff;padding:60px 15px;text-align:center}
.tsd-plans h2{color:#fff;font-size:2.1rem;font-weight:500;margin-bottom:36px}
.tsd-plan-grid{display:flex;flex-wrap:wrap;gap:20px;justify-content:center;align-items:stretch}
.tsd-plan{flex:1 1 280px;max-width:330px;background:#fff;color:#1a1a1a;padding:30px 26px;text-align:left;position:relative;display:flex;flex-direction:column}
.tsd-plan.tsd-popular{border:3px solid #f2c14e}
.tsd-badge{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:#f2c14e;color:#1a1a1a;font-family:'Poppins',Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;padding:5px 14px;border-radius:30px;white-space:nowrap}
.tsd-plan h3{font-size:1.2rem;font-weight:500;letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px}
.tsd-price{font-family:'Oswald',Arial,sans-serif;font-size:2.4rem;font-weight:600;color:#da195b;line-height:1}
.tsd-price em{font-style:normal;font-family:'Roboto',Arial,sans-serif;font-size:15px;font-weight:400;color:#6b6b6b}
.tsd-plan ul{list-style:none;margin:20px 0 26px;padding:0;font-size:14px;line-height:1.55}
.tsd-plan li{padding:8px 0;border-bottom:1px solid #f0f0f0}
.tsd-plan li:last-child{border-bottom:0}
.tsd-plan .tsd-btn{margin-top:auto}
.tsd-plan-text{font-size:14px;line-height:1.6;color:#333;margin:18px 0 0}
.tsd-plan-actions{margin-top:auto;display:flex;flex-wrap:wrap;gap:8px}
.tsd-plan-actions .tsd-btn{margin-top:0;flex:1 1 120px;padding:12px 10px;text-transform:none;letter-spacing:0;font-size:14px;white-space:nowrap}
.tsd-btn.tsd-btn-outline{background:#fff;color:#1a1a1a;border:2px solid #da195b;padding:10px 12px}
.tsd-btn.tsd-btn-outline:hover{background:#fdf0f4;color:#1a1a1a}
.tsd-plan.tsd-tier{text-align:center}
.tsd-plan:not(.tsd-popular){border:3px solid transparent}
.tsd-tier-name{font-size:15px;color:#6b6b6b;margin-bottom:4px}
.tsd-plan.tsd-tier p{font-size:14px;line-height:1.55;color:#333;margin:14px 0 0;padding-bottom:14px;border-bottom:1px solid #f0f0f0}
.tsd-plan.tsd-tier .tsd-price{margin:auto 0 20px;padding-top:26px}
.tsd-plan.tsd-tier .tsd-btn{margin-top:0}
.tsd-note{font-size:12px;color:#9a9a9a;margin-top:28px;max-width:720px;margin-left:auto;margin-right:auto;line-height:1.6}
.tsd-btn{display:inline-block;background:#da195b;color:#fff;font-family:'Poppins',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;text-decoration:none;padding:13px 26px;text-align:center}
.tsd-btn:hover{background:#b91249;color:#fff}
.tsd-cta{padding:62px 15px;text-align:center;border-top:1px solid #ececec}
.tsd-cta h2{font-size:2rem;font-weight:500;margin-bottom:24px}

.tsd-footer{background:#101a2d;background-image:linear-gradient(rgba(16,26,45,.9),rgba(16,26,45,.97)),url('%FOOTER_BG%');background-size:cover;background-position:center;color:#cfd6e4;padding:60px 0 0}
.tsd-footer .tsd-row{align-items:flex-start}
.tsd-footer-col{flex:1 1 210px;padding:0 15px;margin-bottom:34px}
.tsd-footer h4{font-family:'Oswald',Arial,sans-serif;font-size:18px;font-weight:400;line-height:23.4px;text-transform:uppercase;color:#fff;margin-bottom:20px}
.tsd-footer img{height:46px;width:auto;margin-bottom:20px}
.tsd-footer ul{list-style:none;margin:0;padding:0}
.tsd-footer li{margin-bottom:6px;font-size:14px;line-height:30px}
.tsd-footer a{color:#cfd6e4;text-decoration:none}
.tsd-footer a:hover{color:#fff}
.tsd-footer-social{display:flex;gap:9px}
.tsd-footer-social a{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;background:#da195b;color:#fff;font-size:11px;font-weight:700}
.tsd-copyright{border-top:1px solid rgba(255,255,255,.12);margin-top:12px;padding:20px 0;font-size:12px;color:#8e98ab}

@media (max-width:991px){
  .tsd-main,.tsd-side{flex:0 0 100%;max-width:100%}
  .tsd-side{margin-top:50px}
  .tsd-nav{display:none}
  .tsd-article h1{font-size:30px;line-height:1.2}
  .tsd-article h2{font-size:24px;line-height:1.25}
  .tsd-pullquote p{font-size:22px}
}
""".replace("%FOOTER_BG%", TSD_FOOTER_BG)


def _e(value) -> str:
    return escape(str(value or ""), quote=True)


def claim_bar(booking_url: str, text: str = "This is a preview of your Sponsored Story",
              button: str = "Claim This Story") -> str:
    """Ours, not TSD's. Sits above the site header so the prospect reads the
    offer before the chrome, exactly as every other LVRG preview does."""
    return f"""<div class="tsd-claim">
  <span>{text}</span>
  <a href="{_e(booking_url)}">{button} &rarr;</a>
</div>"""


def site_header() -> str:
    items = "\n".join(
        f'        <li><a href="{_e(url)}">{label}</a></li>' for label, url in NAV
    )
    return f"""<header class="tsd-header">
  <div class="tsd-container">
    <div class="tsd-logo"><a href="{TSD_HOME}/"><img src="{TSD_LOGO}" alt="There San Diego" width="200" height="58"></a></div>
    <nav class="tsd-nav">
      <ul>
{items}
        <li><span class="tsd-search">{_icon('search', 16)}<em>Search here..</em></span></li>
      </ul>
    </nav>
  </div>
</header>"""


def disclosure(text: str = DISCLOSURE) -> str:
    return f'<div class="tsd-disclosure">{text}</div>'


# The platforms we can draw, in the order they show, with the name a reader
# knows them by. The old row printed key.title(), which spelt "Youtube" and
# "Linkedin", and put plain grey words where a reader looks for icons.
_SOCIAL_PLATFORMS = [
    ("instagram_url", "instagram", "Instagram"),
    ("facebook_url", "facebook", "Facebook"),
    ("tiktok_url", "tiktok", "TikTok"),
    ("youtube_url", "youtube", "YouTube"),
    ("linkedin_url", "linkedin", "LinkedIn"),
]

_HTTP_URL = re.compile(r"https?://", re.IGNORECASE)


def _social_links(intel: dict) -> list:
    """(icon, name, url) for every social we hold, in platform order.

    Only http(s) addresses: these land in an href, and the app's values come
    from a scrape. A bare "instagram.com/x" gets https:// rather than being
    read as a path on the preview's own host.
    """
    socials = intel.get("socials") or {}
    links = []
    for key, icon, name in _SOCIAL_PLATFORMS:
        url = str(socials.get(key) or "").strip()
        if url and not _HTTP_URL.match(url):
            url = f"https://{url}" if "." in url.split("/")[0] and ":" not in url else ""
        if url:
            links.append((icon, name, url))
    return links


def social_icons(intel: dict) -> str:
    """A row of round icon links, one per social we hold. Empty when none.

    Fixed markup built from intel, never the model's: a social link the model
    wrote could be somebody else's account.
    """
    links = _social_links(intel)
    if not links:
        return ""
    who = intel.get("business_name") or "this business"
    anchors = "".join(
        f'<a class="tsd-social-icon" href="{_e(url)}" target="_blank" rel="noopener" '
        f'title="{_e(name)}" aria-label="{_e(f"{who} on {name}")}">{_icon(icon, 16)}</a>'
        for icon, name, url in links
    )
    return f'<span class="tsd-social">{anchors}</span>'


_FACTBOX = re.compile(
    r"""<div\b[^>]*\bclass=["'][^"']*tsd-factbox[^"']*["'][^>]*>.*?</div>""",
    re.IGNORECASE | re.DOTALL,
)
_LAST_UL_CLOSE = re.compile(r"</ul>(?!.*</ul>)", re.IGNORECASE | re.DOTALL)


def insert_follow_row(article: str, intel: dict) -> str:
    """Put the business's socials at the end of the story (POD01-238).

    As the last line of The Details box when the model wrote one, otherwise as
    a small strip after the last paragraph, so a reader who finishes the story
    has somewhere to follow them. No socials, no row.
    """
    icons = social_icons(intel)
    if not icons:
        return article

    box = _FACTBOX.search(article)
    if box:
        row = f'<li class="tsd-follow"><strong>Follow:</strong> {icons}</li>'
        inner = box.group(0)
        if _LAST_UL_CLOSE.search(inner):
            inner = _LAST_UL_CLOSE.sub(lambda m: row + "</ul>", inner, count=1)
        else:
            inner = inner[:-len("</div>")] + f"<ul>{row}</ul></div>"
        return article[:box.start()] + inner + article[box.end():]

    name = _e(intel.get("business_name") or "them")
    return article.rstrip() + f'\n<div class="tsd-follow-strip"><strong>Follow {name}</strong> {icons}</div>\n'


def _directory(intel: dict, vertical: str = "") -> tuple:
    """The TSD directory this business is filed under. The vertical the user
    picked wins over the scraped business_type, which has no "realtor"."""
    return (DIRECTORIES.get((vertical or "").lower())
            or DIRECTORIES.get((intel.get("business_type") or "").lower(), DEFAULT_DIRECTORY))


def business_details(intel: dict, vertical: str = "", sponsored: bool = False) -> str:
    """The "Business Details" panel, built only from data we actually hold.

    A row is written only when its value exists. The live panel never prints
    "Not listed", and this is the one block a prospect reads as data rather
    than prose, so an empty row here is worse than a short panel.

    `sponsored` marks their website and socials the way a paid listing's are.
    """
    directory = _directory(intel, vertical)
    site = intel.get("page_url") or (f"https://{intel['domain']}" if intel.get("domain") else "")

    rows = [("directory", "Directory", f'<a href="{_e(directory[1])}">{_e(directory[0])}</a>')]

    # Plain text, not a link. TSD files these under /location/<slug>/ and a
    # neighborhood it has no page for would be a 404 in the fact panel.
    neighborhood = intel.get("neighborhood") or (intel.get("location") or "").split(",")[0]
    if neighborhood:
        rows.append(("pin", "Neighborhood", _e(neighborhood)))
    if intel.get("location"):
        rows.append(("address", "Address", _e(intel["location"])))
    if intel.get("phone"):
        tel = "".join(c for c in str(intel["phone"]) if c.isdigit() or c == "+")
        rows.append(("phone", "Phone", f'<a href="tel:{_e(tel)}">{_e(intel["phone"])}</a>'))
    if site:
        rows.append(("website", "Website",
                     f'<a href="{_e(site)}" target="_blank" rel="noopener">{_e(site)}</a>'))
    icons = social_icons(intel)
    if icons:
        rows.append(("social", "Social", icons))

    items = "\n".join(
        f"""    <li>{_icon(icon, 26)}<div>
      <div class="tsd-dt">{label}</div>
      <div class="tsd-dd">{value}</div>
    </div></li>""" for icon, label, value in rows
    )
    panel = f"""<div class="tsd-widget">
  <div class="tsd-widget-title"><span>Business Details</span></div>
  <ul class="tsd-details">
{items}
  </ul>
</div>"""
    return sponsored_links(panel) if sponsored else panel


def whats_hot() -> str:
    items = "\n".join(
        f"""  <div class="tsd-hot">
    <a href="{_e(url)}"><img src="{_e(img)}" alt="" width="78" height="60" loading="lazy"></a>
    <a href="{_e(url)}">{_e(title)}</a>
  </div>""" for title, url, img in WHATS_HOT
    )
    return f"""<div class="tsd-widget">
  <div class="tsd-widget-title"><span>What's Hot</span></div>
{items}
</div>"""


def upcoming_events() -> str:
    blocks = []
    for title, url, img, day, start, venue, place in UPCOMING_EVENTS:
        meta = [f'<span>{_icon("calendar", 13)}{_e(day)}</span>',
                f'<span>{_icon("clock", 13)}{_e(start)}</span>']
        if venue:
            meta.append(f'<span>{_icon("address", 13)}{_e(venue)}</span>')
        meta.append(f'<span>{_icon("pin", 13)}{_e(place)}</span>')
        blocks.append(f"""  <div class="tsd-event">
    <a href="{_e(url)}"><img src="{_e(img)}" alt="" loading="lazy"></a>
    <a class="tsd-event-title" href="{_e(url)}">{_e(title)}</a>
    <div class="tsd-meta">{''.join(meta)}</div>
  </div>""")
    return f"""<div class="tsd-widget">
  <div class="tsd-widget-title"><span>Upcoming Events</span></div>
{chr(10).join(blocks)}
</div>"""


def _permalink(url: str) -> str:
    """A post URL with its tracking query and fragment cut off."""
    return url.split("#", 1)[0].split("?", 1)[0]


def post_embed(post: dict) -> str:
    """One real post, in the platform's own embed markup.

    Server-side and deterministic, never written by the model: every link here
    came back from the platform itself, so a post can never be one the model
    remembered or made up. The fallback inside each blockquote is the link the
    platform's own embed code carries, and it is what shows if their script is
    blocked.
    """
    platform = post.get("platform")
    url = post.get("url") or ""
    if not url.startswith("https://"):
        return ""
    if platform == "instagram":
        link = _e(_permalink(url) + "?utm_source=ig_embed")
        return f"""<div class="tsd-post">
<blockquote class="instagram-media" data-instgrm-permalink="{link}" data-instgrm-version="14" style="background:#fff;border:0;border-radius:3px;box-shadow:0 0 1px 0 rgba(0,0,0,.5),0 1px 10px 0 rgba(0,0,0,.15);margin:0 auto;max-width:540px;min-width:326px;padding:0;width:100%"><a href="{link}" target="_blank" rel="noopener">View this post on Instagram</a></blockquote>
</div>"""
    if platform == "facebook":
        link = _e(url)
        return f"""<div class="tsd-post">
<div class="fb-post" data-href="{link}" data-width="500" data-show-text="true"><blockquote cite="{link}" class="fb-xfbml-parse-ignore"><a href="{link}" target="_blank" rel="noopener">View this post on Facebook</a></blockquote></div>
</div>"""
    if platform == "tiktok" and post.get("embed_id"):
        link = _e(_permalink(url))
        return f"""<div class="tsd-post">
<blockquote class="tiktok-embed" cite="{link}" data-video-id="{_e(post['embed_id'])}" style="max-width:605px;min-width:325px;margin:0 auto"><section><a href="{link}" target="_blank" rel="noopener">View this video on TikTok</a></section></blockquote>
</div>"""
    if platform == "youtube" and post.get("embed_id"):
        return f"""<div class="tsd-post tsd-post-video">
<iframe src="https://www.youtube-nocookie.com/embed/{_e(post['embed_id'])}" title="Video on YouTube" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
</div>"""
    return ""


def post_scripts(posts: list) -> str:
    """Each embed script once, for the platforms these posts are on."""
    used = []
    for post in posts:
        platform = post.get("platform")
        if platform in _EMBED_SCRIPTS and platform not in used:
            used.append(platform)
    return "\n".join(_EMBED_SCRIPTS[p] for p in used)


def photo_card(photo_url: str, intel: dict) -> str:
    """A photo off the business's own website, set like a post: their name on
    top, the photo, and a link to their site. Used when no social post can be
    shown. A click anywhere opens their website."""
    site = intel.get("page_url") or (f"https://{intel['domain']}" if intel.get("domain") else "")
    if not photo_url or not site:
        return ""
    name = intel.get("business_name") or intel.get("domain") or ""
    shown = (intel.get("domain") or urlparse(site).hostname or site).removeprefix("www.")
    link = f'href="{_e(site)}" target="_blank" rel="noopener"'
    return f"""<figure class="tsd-post tsd-sitepost">
  <div class="tsd-embed">
    <span class="tsd-embed-avatar">{_icon('website', 22)}</span>
    <div class="tsd-embed-who">
      <div class="tsd-embed-handle">{_e(name)}</div>
      <div class="tsd-embed-where">{_e(shown)}</div>
    </div>
    <a class="tsd-embed-btn" {link}>Visit website</a>
  </div>
  <a {link}><img src="{_e(photo_url)}" alt="A photo from {_e(name)}" loading="lazy"></a>
  <a class="tsd-sitepost-more" {link}>View more on {_e(shown)}</a>
</figure>"""


# A </p> inside the pull quote, the fact box, a figure or a quote is not the end
# of a story paragraph, and a post dropped there would land inside that block.
_PARAGRAPH_END = re.compile(r"</p>", re.IGNORECASE)
_INNER_BLOCKS = re.compile(
    r"""<div\b[^>]*\bclass=["'][^"']*tsd-(?:pullquote|factbox)[^"']*["'][^>]*>.*?</div>"""
    r"""|<(figure|blockquote)\b.*?</\1>""",
    re.IGNORECASE | re.DOTALL,
)
_SUBHEADING = re.compile(r"<h2\b", re.IGNORECASE)
_HERO_IMG = re.compile(r"""<img\b[^>]*\bclass=["'][^"']*tsd-hero[^"']*["'][^>]*>""", re.IGNORECASE)


def _story_paragraph_ends(article: str) -> list:
    inside = [m.span() for m in _INNER_BLOCKS.finditer(article)]
    return [m.end() for m in _PARAGRAPH_END.finditer(article)
            if not any(a < m.start() < b for a, b in inside)]


def insert_story_posts(article: str, blocks: list, scripts: str = "") -> str:
    """Place up to two posts where the live article puts them.

    The first after two paragraphs of story, where the profile card used to
    sit. The second later on, before a subheading with at least two paragraphs
    between the two, so they never sit side by side in a long story. A story
    too short for that gets them one after the other.
    """
    blocks = [b for b in blocks if b][:2]
    if not blocks:
        return article

    ends = _story_paragraph_ends(article)
    if len(ends) >= 2:
        first = ends[1]
    else:
        # A very short story: after the hero if there is one, else on top.
        hero = _HERO_IMG.search(article)
        first = hero.end() if hero else 0
    positions = [first]

    if len(blocks) > 1:
        later = [e for e in ends if e > first]
        second = first
        if len(later) >= 2:
            heading = next((m.start() for m in _SUBHEADING.finditer(article) if m.start() >= later[1]), None)
            if heading is not None:
                second = heading
            elif len(later) >= 3:
                second = later[len(later) // 2]
        positions.append(second)

    # Last first, so the earlier position still points at the same place. On a
    # shared position the first block then lands in front of the second.
    for at, block in reversed(list(zip(positions, blocks))):
        article = article[:at] + "\n" + block + "\n" + article[at:]
    return article + ("\n" + scripts if scripts else "")


def _with_params(url: str, booking_url: str) -> str:
    """A TSD checkout link carrying the booking link's attribution params, so a
    checkout still says which lead and page it came from."""
    params = booking_url.split("?", 1)[1] if "?" in booking_url else ""
    return f"{url}?{params}" if params else url


def sales_block(booking_url: str, business_name: str) -> str:
    """The offer itself: guarantee, reach, the three tiers, the closing CTA.

    Fixed markup rather than prompt text. Every number here is TSD's own
    published figure, and a model rewriting them is a model editing the price
    list on a page the prospect will be quoted from. One build turned the
    monthly plans into one-time fees.
    """
    stats = "\n".join(
        f'        <div class="tsd-stat"><b>{value}</b><span>{label}</span></div>'
        for value, label in REACH
    )
    cards = []
    for name, price, description, features, popular, checkout in PLANS:
        badge = '<span class="tsd-badge">Most Popular</span>' if popular else ""
        items = "\n".join(f"          <li>{f}</li>" for f in [*features, *PLAN_COMMON_FEATURES])
        cards.append(f"""      <div class="tsd-plan{' tsd-popular' if popular else ''}">{badge}
        <h3>{name}</h3>
        <div class="tsd-price">{price}<em>/month</em></div>
        <p class="tsd-plan-text">{description}</p>
        <ul>
{items}
        </ul>
        <div class="tsd-plan-actions">
          <a class="tsd-btn" href="{_e(_with_params(checkout, booking_url))}" target="_blank" rel="noopener">Get Started</a>
          <a class="tsd-btn tsd-btn-outline" href="{_e(booking_url)}">Speak with a Rep</a>
        </div>
      </div>""")
    return f"""<section class="tsd-guarantee">
  <p>{GUARANTEE}</p>
</section>

<section class="tsd-reach">
  <div class="tsd-container">
    <h2>There San Diego's Reach</h2>
    <div class="tsd-reach-grid">
{stats}
    </div>
  </div>
</section>

<section class="tsd-plans">
  <div class="tsd-container">
    <h2>Choose Your Sponsored Story Plan</h2>
    <div class="tsd-plan-grid">
{chr(10).join(cards)}
    </div>
    <p class="tsd-note">{PLANS_FOOTNOTE}</p>
  </div>
</section>

<section class="tsd-cta">
  <h2>Ready to see {_e(business_name)} in front of 70,000+ San Diegans?</h2>
  <a class="tsd-btn" href="{_e(booking_url)}">Claim This Story &rarr;</a>
</section>"""


# ── Get Listed: the Business Profile (POD01-251) ─────────────────────────────
# Measured off the two live profiles, Josh Taylor and Elements Design & Build:
# the same site chrome as the story, no byline, the facts as a plain list under
# "Credentials & Details", the owner's quote as a centred blockquote under a big
# opening mark (gt-style.min.css: 6rem on a 13px root), and the project photos
# as a two-column gallery just above the closing call to action.

# Platforms whose handle is how a reader knows the account. The rest read
# better as the business name, the way the live list shows Yelp.
_HANDLE_PLATFORMS = ("instagram", "tiktok", "youtube")


def _social_label(icon: str, url: str, intel: dict) -> str:
    path = [p for p in urlparse(url).path.split("/") if p]
    if icon in _HANDLE_PLATFORMS and path:
        return "@" + path[-1].lstrip("@")
    return intel.get("business_name") or urlparse(url).hostname or url


def _shown_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    path = urlparse(url).path.rstrip("/")
    return host + path if host else url


def credentials_list(intel: dict, own_site_url: str, vertical: str = "") -> str:
    """"Credentials & Details": the hard facts, one row each, only the ones we
    hold. Fixed markup, never the model's: a phone number or a handle the model
    wrote could be somebody else's.

    The licence row leads, as on the live profiles, and only when their own
    site states the number (POD01-272). The live row also names the owner and
    title ("Josh Taylor, Realtor® | CA DRE #..."); the scrape has a first name
    at best, so the row is the licence alone rather than a guessed name.
    """
    rows = []
    licence = _licence_row(intel, vertical)
    if licence:
        rows.append(licence)
    if intel.get("business_name"):
        rows.append(f"Company: {_e(intel.get('business_name'))}")
    services = [s for s in (intel.get("services") or []) if str(s).strip()]
    if services:
        rows.append("Specialties: " + _e(", ".join(str(s) for s in services[:4])))
    location = (intel.get("location") or "").strip()
    if location:
        row = f"Address: {_e(location)}"
        # A map link only for a street address. "San Diego, CA" on a map is a
        # pin on City Hall.
        if any(c.isdigit() for c in location):
            # Encoded, not joined with "+": a raw "#" in "#205" ended the URL
            # there, and Felicia Lewis Group's link searched "5965 Village Way"
            # and landed on the UPS Store next door.
            maps = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(location)
            row += f' (<a href="{_e(maps)}" target="_blank" rel="noopener">Google Maps</a>)'
        rows.append(row)
    if intel.get("neighborhood"):
        rows.append(f"Neighborhood: {_e(intel['neighborhood'])}")
    if intel.get("phone"):
        rows.append(f"Phone: {_e(intel['phone'])}")
    if intel.get("email"):
        rows.append(f'Email: <a href="mailto:{_e(intel["email"])}">{_e(intel["email"])}</a>')
    if own_site_url:
        rows.append(f'Website: <a href="{_e(own_site_url)}" target="_blank" rel="noopener">'
                    f"{_e(_shown_domain(own_site_url))}</a>")
    if intel.get("hours"):
        rows.append(f"Hours: {_e(intel['hours'])}")
    for icon, name, url in _social_links(intel):
        rows.append(f'{name}: <a href="{_e(url)}" target="_blank" rel="noopener">'
                    f"{_e(_social_label(icon, url, intel))}</a>")

    items = "\n".join(f"  <li>{row}</li>" for row in rows)
    return f"<h2>Credentials &amp; Details</h2>\n<ul>\n{items}\n</ul>"


# The licence each vertical carries on the live profiles, in their wording.
_LICENCE_LABELS = {
    "realtor": ("dre", "CA DRE #"),
    "contractor": ("cslb", "CSLB License #"),
}


def _licence_row(intel: dict, vertical: str) -> str:
    key, label = _LICENCE_LABELS.get((vertical or "").lower(), ("", ""))
    number = (intel.get("licences") or {}).get(key) if key else ""
    return f"{label}{_e(number)}" if number else ""


# What ThereSanDiego puts on every link to a paid listing's business, measured
# off the live Josh Taylor, Elements and Iron Orr profiles on 2 Oct 2026.
SPONSORED_REL = "sponsored nofollow noopener"
_A_TAG = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
_HREF_ATTR = re.compile(r"""\bhref\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_REL_ATTR = re.compile(r"""\s+rel\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)


def sponsored_links(html: str) -> str:
    """Every outbound link in `html` marked as paid, the way TSD marks a
    listing's website, socials and map link (POD01-272). Its own links
    (directory, guides) and mailto/tel stay as they are, as on the live page."""
    def _mark(match: "re.Match") -> str:
        tag = match.group(0)
        href = _HREF_ATTR.search(tag)
        if not href or not _HTTP_URL.match(href.group(1)):
            return tag
        host = (urlparse(href.group(1)).hostname or "").lower()
        if host == "theresandiego.com" or host.endswith(".theresandiego.com"):
            return tag
        return _REL_ATTR.sub("", tag)[:-1] + f' rel="{SPONSORED_REL}">'
    return _A_TAG.sub(_mark, html)


def photo_gallery(photo_urls: list, business_name: str) -> str:
    """The profile's photo gallery, from their own website photos. Empty when
    there are none: a gallery of placeholders is worse than no gallery."""
    photo_urls = [u for u in photo_urls if u]
    if not photo_urls:
        return ""
    figures = "\n".join(
        f'  <figure><img src="{_e(url)}" alt="A photo from {_e(business_name)}" loading="lazy"></figure>'
        for url in photo_urls
    )
    return f'<div class="tsd-gallery">\n{figures}\n</div>'


def listing_offer_block(booking_url: str, business_name: str) -> str:
    """The Get Listed offer: reach, the three tier cards, the closing CTA.

    Fixed markup for the same reason as sales_block: this is the price list the
    prospect will be quoted from, so the model never writes it.

    Each card opens the same TSD page its button opens on the live pricing page,
    carrying the booking link's attribution params so a checkout still says
    which lead and page it came from.
    """
    stats = "\n".join(
        f'        <div class="tsd-stat"><b>{value}</b><span>{label}</span></div>'
        for value, label in REACH
    )
    cards = []
    for tier, name, text, note, price, per, button, url, recommended in GET_LISTED_TIERS:
        badge = '<span class="tsd-badge">Recommended</span>' if recommended else ""
        href = _with_params(url, booking_url)
        cards.append(f"""      <div class="tsd-plan tsd-tier{' tsd-popular' if recommended else ''}">{badge}
        <h3>{tier}</h3>
        <div class="tsd-tier-name">{name}</div>
        <p>{text}</p>{f'{chr(10)}        <p class="tsd-tier-note">{note}</p>' if note else ''}
        <div class="tsd-price">{price}{f'<em> {per}</em>' if per else ''}</div>
        <a class="tsd-btn" href="{_e(href)}" target="_blank" rel="noopener">{button} &rarr;</a>
      </div>""")
    return f"""<section class="tsd-reach">
  <div class="tsd-container">
    <h2>There San Diego's Reach</h2>
    <div class="tsd-reach-grid">
{stats}
    </div>
  </div>
</section>

<section class="tsd-plans">
  <div class="tsd-container">
    <h2>Get Listed Pricing Plan</h2>
    <div class="tsd-plan-grid">
{chr(10).join(cards)}
    </div>
  </div>
</section>

<section class="tsd-cta">
  <h2>Ready to see {_e(business_name)} listed on ThereSanDiego.com?</h2>
  <a class="tsd-btn" href="{_e(booking_url)}">Claim This Listing &rarr;</a>
</section>"""


def site_footer() -> str:
    columns = []
    for heading, links in FOOTER_LINKS:
        rows = "\n".join(f'          <li><a href="{_e(url)}">{label}</a></li>'
                         for label, url in links)
        columns.append(f"""      <div class="tsd-footer-col">
        <h4>{heading}</h4>
        <ul>
{rows}
        </ul>
      </div>""")
    socials = "".join(
        f'<a href="{_e(url)}" aria-label="{label}">{label[0]}</a>'
        for label, url in FOOTER_SOCIALS
    )
    return f"""<footer class="tsd-footer">
  <div class="tsd-container">
    <div class="tsd-row">
      <div class="tsd-footer-col">
        <h4>Things To Do In San Diego</h4>
        <img src="{TSD_FOOTER_LOGO}" alt="There San Diego">
        <div class="tsd-footer-social">{socials}</div>
      </div>
{chr(10).join(columns)}
      <div class="tsd-footer-col">
        <h4>Contact Us</h4>
        <ul>
          <li>{FOOTER_ADDRESS}</li>
          <li><a href="mailto:{FOOTER_EMAIL}">{FOOTER_EMAIL}</a></li>
          <li><a href="tel:+16193504686">{FOOTER_PHONE}</a></li>
        </ul>
      </div>
    </div>
    <div class="tsd-copyright">Copyright &copy; 2016 There Media Group, LLC All rights reserved</div>
  </div>
</footer>"""


def render_story_page(article: str, intel: dict, booking_url: str, title: str) -> str:
    """Drop the model's article into the TSD chrome and return a whole page."""
    return _render_page(
        article, intel, title, claim_bar(booking_url),
        sales_block(booking_url, intel.get("business_name", "")),
        notice=STORY_DISCLOSURE,
    )


def render_profile_page(article: str, intel: dict, booking_url: str, title: str,
                        vertical: str = "") -> str:
    """The Get Listed Business Profile: the same chrome, the listing's claim bar
    and the one $297 offer instead of the monthly story plans."""
    return _render_page(
        article, intel, title,
        claim_bar(booking_url, "This is a preview of your ThereSanDiego.com listing",
                  "Claim This Listing"),
        listing_offer_block(booking_url, intel.get("business_name", "")),
        vertical,
        sponsored=True,
    )


def _render_page(article: str, intel: dict, title: str, claim: str, offer: str,
                 vertical: str = "", notice: str = DISCLOSURE, sponsored: bool = False) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@300;400;500;600&amp;family=Poppins:wght@400;500;600;700&amp;family=Roboto:wght@300;400;500;700&amp;display=swap" rel="stylesheet">
<style>{STYLES}</style>
</head>
<body>
{claim}
{site_header()}

<div class="tsd-container">
  {disclosure(notice)}
  <div class="tsd-row">
    <div class="tsd-main">
      <article class="tsd-article">
{article}
      </article>
    </div>
    <aside class="tsd-side">
{business_details(intel, vertical, sponsored)}
{whats_hot()}
{upcoming_events()}
    </aside>
  </div>
</div>

{offer}
{site_footer()}
</body>
</html>"""
