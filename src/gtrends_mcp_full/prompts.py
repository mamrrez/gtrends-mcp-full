"""Prompts and reference resources.

Prompts are ready-made workflows a client can offer in its menu: each one
tells the model which tools to chain and what the finished answer should
contain. The resources are short reference pages a model can read instead of
guessing.
"""

from __future__ import annotations

from .client import TREND_CATEGORIES
from .runtime import Runtime

GUIDE = """# Reading Google Trends

## What the numbers are
- Every value is an index from 0 to 100. Google takes the share of all searches that a term had at each
  moment, then rescales the whole answer so its highest point is 100.
- It is never a count of searches. A term at 50 was searched half as often *relative to total search
  volume* as at its peak.
- Two separate requests are on two separate scales. Only terms inside the same request can be compared.
  compare_many, share_of_search, compare_periods and compare_locations build one scale for you.
- "<1" means a trace of interest, 0 means too little to measure. The last point of a range is usually
  partial (still being collected) and is marked.
- Across locations the value is the term's share of *that place's* searches: a small country can outrank a
  large one.

## Search terms, topics and operators
- A search term matches searches containing those words in that language and spelling.
- A topic (find_topic → an id such as /m/0dr90d) matches every search about the thing in any language.
- `a + b` = either; `a -b` = a without b; `"a b"` = the exact phrase. No operators inside topics.
- A category id (find_category) narrows an ambiguous term to one meaning.

## Time ranges and what comes back
| range | granularity |
|---|---|
| 1h, 4h | per minute |
| 1d | per 8 minutes |
| 7d | hourly |
| up to ~9 months | daily |
| up to 5 years | weekly |
| longer, or `all` (2004 →) | monthly |
Accepted: 1h 4h 1d 7d 1m 3m 12m 5y all · 6m, 2y, 45d · 2024 · 2024-03 · 2019-2023 ·
"2024-01-01 2024-12-31" · "2026-09-28T00 2026-09-30T00" (hourly, at most 7 days).

## Rising and Breakout
Rising related queries are ranked by growth against the previous period. "Breakout" means growth above
5000% — typically a query that barely existed before.

## Trending Now
The Trending Now list carries a real, bucketed search volume ("200K+"), a growth percentage, a start
time and the queries that make up each trend. Google keeps a trend for about a week; snapshot_trending
saves it locally so it can be searched later.

## Limits to keep in mind
- Google Trends is a sample, so two identical requests on different days can differ by a point or two.
- Low-volume terms come back as zeros or not at all; widen the range or the location.
- This server uses the endpoints of the Trends website. They are rate-limited: ask for several keywords
  in one call, and expect a cached answer when Google refuses a fresh one.
"""


def register(mcp, rt: Runtime) -> None:
    @mcp.resource("gtrends://guide", name="Reading Google Trends", description="How to read the 0-100 index, time ranges, operators, topics and Trending Now.", mime_type="text/markdown")
    def guide() -> str:
        return GUIDE

    @mcp.resource("gtrends://trending-categories", name="Trending Now categories", description="The category names that trending_now accepts as a filter.", mime_type="text/markdown")
    def trending_categories() -> str:
        return "# Trending Now categories\n\n" + "\n".join(f"- {name}" for name in sorted(TREND_CATEGORIES.values()))

    @mcp.prompt(name="trend_report", title="Trend report for a keyword", description="A full read on one keyword: direction, seasonality, places, related searches and what to do about it.")
    def trend_report(keyword: str, geo: str = "") -> str:
        where = f" in {geo}" if geo else ""
        return (
            f"Write a trend report for “{keyword}”{where} using the Google Trends tools.\n\n"
            f"1. keyword_overview(keyword, timeframe='12m'{', geo=' + repr(geo) if geo else ''}) for the current picture.\n"
            "2. trend_momentum(keyword) for the five-year direction, and seasonality(keyword) for the yearly rhythm.\n"
            "3. find_spikes(keyword) — date the two or three largest spikes and say what probably caused each "
            "(say so when you are guessing).\n"
            "4. related_queries(keyword) — group the rising queries into themes.\n\n"
            "Finish with: a one-paragraph verdict (growing, fading, seasonal, event-driven), the best months to "
            "publish or campaign, three content or product angles from the rising queries, and anything the data "
            "cannot tell us. Quote index values as relative interest, never as search counts."
        )

    @mcp.prompt(name="newsjacking_brief", title="Newsjacking brief", description="Today's trends that touch your subjects, with the story behind each and an angle to take.")
    def newsjacking_brief(subjects: str, geo: str = "") -> str:
        loc = geo or "the default location"
        return (
            f"Find today's search trends in {loc} that a brand covering these subjects could credibly join: {subjects}.\n\n"
            f"1. match_trends(terms='{subjects}'{', geo=' + repr(geo) if geo else ''}, hours=48).\n"
            "2. If nothing matches, call trending_now with the closest category and pick trends with a genuine link "
            "to the subjects — and say plainly when there is no good fit rather than forcing one.\n"
            "3. For up to three candidates call trend_details to get the queries people type and the news behind it.\n\n"
            "For each candidate give: what happened (from the headlines), how big and how fresh the trend is (volume, "
            "growth, active or ended), the exact queries to target, a content angle, and a risk note if the story is "
            "tragic, political or otherwise unsuitable for a brand. Rank them by fit, not by size."
        )

    @mcp.prompt(name="seasonal_content_plan", title="Seasonal content plan", description="A publishing calendar for a list of topics, built from their five-year seasonality.")
    def seasonal_content_plan(topics: str, geo: str = "", lead_weeks: str = "8") -> str:
        return (
            f"Build a publishing plan for these topics: {topics}.\n\n"
            f"1. content_calendar(keywords='{topics}'{', geo=' + repr(geo) if geo else ''}, lead_weeks={lead_weeks}) — up to 8 topics per call.\n"
            "2. For the two topics that need publishing soonest, call seasonality for the month-by-month index and "
            "related_queries for what people search around them.\n\n"
            "Deliver a month-by-month plan for the next twelve months: what to publish, when it must be live, when "
            "demand peaks, and which evergreen topics can fill the quiet months. Flag any topic whose long-run "
            "direction is declining."
        )

    @mcp.prompt(name="market_comparison", title="Brand vs competitors", description="Share of search for a brand against its competitors, where each one wins, and what is rising around them.")
    def market_comparison(brand: str, competitors: str, geo: str = "") -> str:
        g = f", geo={geo!r}" if geo else ""
        return (
            f"Compare “{brand}” with its competitors ({competitors}) on Google Trends.\n\n"
            f"1. find_topic for each name that is also an ordinary word, and use the topic id for those.\n"
            f"2. share_of_search(brand='{brand}', competitors='{competitors}', timeframe='12m'{g}), then again with "
            "timeframe='5y' for the long view.\n"
            f"3. interest_by_region(keywords='{brand}, {competitors}'{g}) to see who leads where.\n"
            f"4. related_queries for the brand and for its strongest competitor — what are people asking about each?\n\n"
            "Report: current share and its direction for every brand, the regions where the brand under-performs, "
            "what the rising queries reveal about each brand's momentum or problems, and two actions. Share of "
            "search is relative interest among these names only; say so once."
        )
