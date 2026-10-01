"""Every tool, called through the MCP server, against the fake Google."""

import asyncio
import re

import pytest

from conftest import call


def rows(out: str) -> list[list[str]]:
    """The cells of every Markdown table row in a tool's output, without header separators."""
    return [[c.strip() for c in line.strip().strip("|").split("|")] for line in out.splitlines() if line.startswith("| ") and not set(line) <= set("|- ")]


def row_for(out: str, first_cell: str) -> list[str]:
    return next(r for r in rows(out) if r[0] == first_cell or (len(r) > 1 and r[1] == first_cell))


# -- registry --------------------------------------------------------------------


def test_every_tool_has_a_description_and_annotations(server):
    tools = server.mcp._tool_manager.list_tools()
    assert len(tools) == 32
    for t in tools:
        assert t.description and len(t.description.strip().splitlines()[0]) > 30, t.name
        assert t.annotations is not None, t.name
    by_name = {t.name: t for t in tools}
    assert by_name["trending_now"].annotations.read_only_hint is True
    assert by_name["snapshot_trending"].annotations.read_only_hint is False
    assert by_name["watchlist_remove"].annotations.destructive_hint is True
    assert by_name["history_sql"].annotations.open_world_hint is False


def test_prompts_and_resources_are_registered(server):
    prompts = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    assert prompts == {"trend_report", "newsjacking_brief", "seasonal_content_plan", "market_comparison"}
    uris = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
    assert uris == {"gtrends://guide", "gtrends://trending-categories"}
    text = asyncio.run(server.mcp.get_prompt("trend_report", {"keyword": "tesla", "geo": "US"})).messages[0].content.text
    assert "keyword_overview" in text and "geo='US'" in text


# -- lookups ---------------------------------------------------------------------


def test_get_capabilities(server):
    out = call(server, "get_capabilities")
    assert "gtrends-mcp-full" in out and "No API key" in out and "trending_now" in out and "backing off:** no" in out


def test_check_endpoints(server):
    out = call(server, "check_endpoints")
    assert "8 of 8 working" in out and "FAILED" not in out


def test_find_location(server):
    assert row_for(call(server, "find_location", query="cali"), "US-CA")[1] == "California"
    assert row_for(call(server, "find_location", query="ir"), "IR")[1] == "Iran"  # a code matches too
    assert "more rows not shown" not in call(server, "find_location", inside="US", limit=1)  # what is inside a place is listed whole
    inside = call(server, "find_location", inside="IR")
    assert "IR-23" in inside and "IR-10" in inside and "Iran (IR)" in inside
    assert "Nothing found" in call(server, "find_location", query="atlantis")
    assert call(server, "find_location").startswith("Error:")


def test_find_category_and_topic(server):
    assert row_for(call(server, "find_category", query="vehicle"), "47")[1] == "Autos & Vehicles"
    assert "No category matches" in call(server, "find_category", query="zzz")
    topics = call(server, "find_topic", text="tesla")
    assert "/m/0dr90d" in topics and "Automotive company" in topics
    assert "knows no topic" in call(server, "find_topic", text="qwertyuiop")


def test_clear_cache(server):
    call(server, "find_topic", text="tesla")
    assert re.search(r"Removed \d+ saved answer", call(server, "clear_cache", what="autocomplete"))
    assert "Session cookie and back-off state cleared" in call(server, "clear_cache", what="expired", reset_session=True)
    from conftest import trust

    trust(server.rt.client())
    assert call(server, "clear_cache", what="everything").startswith("Error:")


# -- trending --------------------------------------------------------------------


def test_trending_now_lists_sorts_and_filters(server):
    out = call(server, "trending_now", geo="US")
    assert "United States (US)" in out and "5 trends, 3 still active" in out and "Technology 2" in out
    assert [r[1] for r in rows(out)[1:]] == ["champions league", "iphone 18", "ai news", "rain tomorrow", "café opening"]
    assert row_for(out, "iphone 18")[2:4] == ["200K+", "+800%"]
    assert "ended · lasted 16 h" in out and "ucl draw, real madrid" in out
    assert [r[1] for r in rows(call(server, "trending_now", geo="US", status="active"))[1:]] == ["champions league", "ai news", "rain tomorrow"]
    assert [r[1] for r in rows(call(server, "trending_now", geo="US", category="tech"))[1:]] == ["iphone 18", "ai news"]
    assert [r[1] for r in rows(call(server, "trending_now", geo="US", sort_by="recent"))[1:]][0] == "ai news"
    assert [r[1] for r in rows(call(server, "trending_now", geo="US", min_volume=100000))[1:]] == ["champions league", "iphone 18"]
    assert "1 more rows not shown" in call(server, "trending_now", geo="US", limit=4)


def test_trending_now_contains_matches_words_not_substrings(server):
    assert [r[1] for r in rows(call(server, "trending_now", geo="US", contains="ai"))[1:]] == ["ai news"]  # not «rain tomorrow»
    assert [r[1] for r in rows(call(server, "trending_now", geo="US", contains="OPENAI"))[1:]] == ["ai news"]  # a query inside the trend, any case
    assert [r[1] for r in rows(call(server, "trending_now", geo="IR", contains="طلا"))[1:]] == ["قیمت دلار"]
    assert "No trend matches the filters" in call(server, "trending_now", geo="US", contains="zebra")


def test_trending_now_defaults_and_errors(server):
    assert "United States (US)" in call(server, "trending_now")  # no worldwide list: falls back to US
    assert "lists no trends" in call(server, "trending_now", geo="JP")
    assert "not a location code" in call(server, "trending_now", geo="Tehran")
    assert "No Trending Now category matches" in call(server, "trending_now", geo="US", category="knitting")
    assert call(server, "trending_now", geo="US", status="maybe").startswith("Error:")
    assert call(server, "trending_now", geo="US", sort_by="size").startswith("Error:")


def test_trend_details(server):
    out = call(server, "trend_details", trend="iphone", geo="US")
    assert out.startswith("# iphone 18") and "200K+" in out and "iphone 18 price, apple event" in out
    assert "Headline about it" in out and "https://news.example/a" in out and "Past 7 days" in out
    assert "Past 7 days" not in call(server, "trend_details", trend="iphone 18", geo="US", with_chart=False, news=0)
    assert "No trend in" in call(server, "trend_details", trend="zebra", geo="US")


def test_trending_feed(server):
    out = call(server, "trending_feed", geo="US", limit=2)
    assert "**1. champions league** — 500K+ searches" in out and "https://news.example/0" in out and "3 more not shown" in out


def test_trending_across_countries_groups_the_same_story(server):
    out = call(server, "trending_across_countries", geos="US,GB,DE")
    r = row_for(out, "champions league")
    assert r[1] == "3"  # title in US and GB, and among Germany's queries under another title
    assert "US 500K+" in r[2] and "GB 200K+" in r[2] and "DE 100K+" in r[2]
    assert "iphone 18" not in out
    assert "iphone 18" in call(server, "trending_across_countries", geos="US,GB", min_countries=1)
    assert call(server, "trending_across_countries", geos="US,GB,DE,IR,JP,FR,BR,AU,CA,IN,IT,ES,NL").startswith("Error:")


def test_match_trends(server):
    out = call(server, "match_trends", terms="AI, Apple, knitting", geo="US")
    assert row_for(out, "iphone 18")[1] == "Apple" and row_for(out, "ai news")[1] == "AI"
    assert "rain tomorrow" not in out
    assert row_for(out, "ai news")[5] == "ai news"  # the title is not repeated
    assert "None of them touches" in call(server, "match_trends", terms="knitting", geo="US")


# -- explore ---------------------------------------------------------------------


def test_interest_over_time(server):
    out = call(server, "interest_over_time", keywords="tesla, byd", geo="US", points=6)
    assert "past 12 months · United States (US) · Web Search · weekly points" in out
    assert row_for(out, "tesla")[1] == "100" and row_for(out, "byd")[1] == "10"
    assert "each row is the mean of the 9 points from that moment on" in out and "still being collected" in out
    assert re.fullmatch(r"from \d{4}-\d{2}-\d{2}", rows(out)[4][0]) and rows(out)[-1][0].endswith("*")
    assert "https://trends.google.com/trends/explore?geo=US&q=tesla,byd" in out
    assert "## Data" not in call(server, "interest_over_time", keywords="tesla", points=0)


def test_interest_over_time_options(server):
    out = call(server, "interest_over_time", keywords="tesla", timeframe="7d", property="youtube", category=47)
    assert "past 7 days" in out and "YouTube Search" in out and "Autos & Vehicles" in out and "hourly points" in out
    assert "| Tesla |" in call(server, "interest_over_time", keywords="/m/0dr90d")  # a topic id shows its name
    cat = call(server, "interest_over_time", category=47)
    assert "| Autos & Vehicles |" in cat
    assert "too little search volume" in call(server, "interest_over_time", keywords="nothing")


def test_interest_over_time_errors(server):
    assert "At most 5 keywords" in call(server, "interest_over_time", keywords="a,b,c,d,e,f")
    assert "Could not understand the time range" in call(server, "interest_over_time", keywords="tesla", timeframe="next week")
    assert "Give at least one keyword" in call(server, "interest_over_time")
    assert "property must be one of" in call(server, "interest_over_time", keywords="tesla", property="tiktok")


def test_interest_by_region(server):
    one = call(server, "interest_by_region", keywords="tesla", geo="US")
    assert "# Interest by region" in one and [r[1] for r in rows(one)[1:]] == ["California", "Texas", "Wyoming"]
    two = call(server, "interest_by_region", keywords="tesla, byd", geo="US")
    assert row_for(two, "California")[2:5] == ["91%", "9%", "tesla"] and "tesla leads in 3 of 3" in two
    assert "Small Place" in call(server, "interest_by_region", keywords="tesla", geo="US", include_low_volume=True)
    assert "# Interest by city" in call(server, "interest_by_region", keywords="tesla", geo="US", resolution="city")
    assert "# Interest by country" in call(server, "interest_by_region", keywords="tesla")
    assert "No place has enough search volume" in call(server, "interest_by_region", keywords="tesla", geo="DE")


def test_related_queries(server):
    out = call(server, "related_queries", keyword="tesla")
    assert row_for(out, "tesla 2027")[1] == "Breakout" and row_for(out, "tesla review")[1] == "55"
    assert out.index("## Rising") < out.index("## Top")
    assert "no related queries" in call(server, "related_queries", keyword="nothing")
    assert "At most 1 keywords" in call(server, "related_queries", keyword="a, b")


def test_related_topics_says_when_google_withholds_them(server):
    out = call(server, "related_topics", keyword="tesla")
    assert "anonymous sessions" in out and "not evidence" in out and "GTRENDS_COOKIE" in out
    server.http.user_type = "USER_TYPE_LEGIT_USER"
    try:
        signed_in = call(server, "related_topics", keyword="byd")
        assert "/m/0dr90d" in signed_in and "Automotive company" in signed_in
    finally:
        server.http.user_type = "USER_TYPE_SCRAPER"


def test_compare_periods(server):
    out = call(server, "compare_periods", keyword="rising", period="2026-06-01 2026-08-31", points=5)
    assert "2026-06-01 → 2026-08-31" in out and "2025-06-01 → 2025-08-31" in out
    change = re.search(r"\*\*Change:\*\* \+(\d+)%", out)
    assert change and 15 < int(change.group(1)) < 35
    assert "Both periods are on one scale" in out and "## Side by side" in out
    prev = call(server, "compare_periods", keyword="rising", period="2026-06-01 2026-08-31", against="previous")
    assert "2026-03-01 → 2026-05-31" in prev
    assert "2024-01-01 → 2024-03-31" in call(server, "compare_periods", keyword="rising", period="2025-01-01 2025-03-31", against="2024-01-01 2024-03-31")
    assert call(server, "compare_periods", keyword="rising", period="7d").startswith("Error:")


def test_compare_locations(server):
    out = call(server, "compare_locations", keyword="tesla", geos="US, DE")
    assert "United States (US)" in out and "Germany (DE)" in out and "not that more people searched" in out
    assert "at least two location codes" in call(server, "compare_locations", keyword="tesla", geos="US")


def test_keyword_overview(server):
    out = call(server, "keyword_overview", keyword="rising", geo="US", timeframe="5y")
    assert "direction:** rising (+" in out and "year over year" in out
    short = call(server, "keyword_overview", keyword="air conditioner", geo="US", timeframe="12m")
    assert "may be the season" in short and "direction:** declining" not in short and "direction:** rising" not in short
    assert "California 100" in out and "rising 2027 (Breakout)" in out and "rising price 100" in out


def test_keyword_overview_survives_a_part_that_fails(server):
    call(server, "clear_cache", what="all")
    server.http.fail = []
    out_ok = call(server, "keyword_overview", keyword="fading", geo="US")
    assert "## Top places" in out_ok
    call(server, "clear_cache", what="regions")
    call(server, "clear_cache", what="related")
    real = server.http._answer

    def flaky(url, params, data):
        if "comparedgeo" in url:
            from conftest import FakeResponse

            return FakeResponse(404, "")
        return real(url, params, data)

    server.http._answer = flaky
    try:
        out = call(server, "keyword_overview", keyword="fading", geo="US")
    finally:
        server.http._answer = real
    assert "Places could not be loaded" in out and "## Top queries" in out


# -- analysis --------------------------------------------------------------------


def test_compare_many_ranks_more_than_five_terms_on_one_scale(server):
    out = call(server, "compare_many", keywords="big, tesla, brand a, brand b, brand c, byd, tiny, nothing", geo="US")
    order = [r[1] for r in rows(out)[1:]]
    assert order == ["big", "tesla", "brand a", "brand b", "brand c", "byd", "tiny", "nothing"]
    avg = {r[1]: float(r[2]) for r in rows(out)[1:]}
    assert avg["big"] == 100.0
    # the true sizes are 90 / 80 / 60 / 30 / 10 / 8 — ratios must survive the regrouping
    assert abs(avg["tesla"] - 88.9) < 2.5 and abs(avg["brand a"] - 66.7) < 2.5 and abs(avg["brand b"] - 33.3) < 2.5 and abs(avg["byd"] - 8.9) < 2.5
    assert "2 groups" in out


def test_compare_many_with_a_named_anchor_and_few_terms(server):
    out = call(server, "compare_many", keywords="brand a, brand b, brand c, tesla, big, byd", anchor="brand b")
    assert "anchor “brand b”" in out and [r[1] for r in rows(out)[1:]][0] == "big"
    small = call(server, "compare_many", keywords="brand a, brand b")
    assert "1 group" in small and [r[1] for r in rows(small)[1:]] == ["brand a", "brand b"]
    assert "At most 25 keywords" in call(server, "compare_many", keywords=", ".join(f"k{i}" for i in range(26)))
    assert "at least 2 keywords" in call(server, "compare_many", keywords="solo")


def test_seasonality(server):
    out = call(server, "seasonality", keyword="air conditioner")
    assert "**Strongly seasonal.** Peak in Jul" in out and "low in Jan" in out and "100% of 4 full years" in out
    assert row_for(out, "Jul")[3] == "peak" and row_for(out, "Jan")[3] == "low"
    assert "## When to publish" in out and "peaks in Jul" in out and "## Next six months" in out
    flat = call(server, "seasonality", keyword="rising")
    assert "**Not seasonal.**" in flat and "## When to publish" not in flat and "peak |" not in flat
    assert "+" in row_for(flat, "2024")[2]  # year-over-year growth is still reported
    assert "at least two years" in call(server, "seasonality", keyword="rising", timeframe="12m")


def test_trend_momentum(server):
    out = call(server, "trend_momentum", keywords="rising, fading, air conditioner, newcomer, nothing, brand a")
    label = {r[0]: r[1] for r in rows(out)[1:]}
    assert label == {"rising": "rising", "fading": "declining", "air conditioner": "stable", "newcomer": "new", "nothing": "no data", "brand a": "stable"}
    assert [r[0] for r in rows(out)[1:]][0] == "newcomer" and [r[0] for r in rows(out)[1:]][-1] == "nothing"
    assert row_for(out, "fading")[2].startswith("-")
    assert "At most 8 keywords" in call(server, "trend_momentum", keywords="a,b,c,d,e,f,g,h,i")


def test_find_spikes(server):
    out = call(server, "find_spikes", keyword="spiky", timeframe="12m")
    r = rows(out)[1]
    assert r[1] == "5.0×" and r[2] == "100" and re.fullmatch(r"[12] weeks?", r[4])
    assert "1 spike at 2× or more" in out
    assert "No stretch reached" in call(server, "find_spikes", keyword="brand a")
    assert call(server, "find_spikes", keyword="spiky", threshold=1.0).startswith("Error:")


def test_share_of_search(server):
    out = call(server, "share_of_search", brand="brand b", competitors="brand a, brand c")
    share = {r[0]: float(r[1].rstrip("%")) for r in rows(out)[1:]}
    assert abs(share["brand b (you)"] - 30) < 0.5 and abs(share["brand a"] - 60) < 0.5 and abs(share["brand c"] - 10) < 0.5
    assert [r[0] for r in rows(out)[1:]] == ["brand a", "brand b (you)", "brand c"]
    assert "**brand b** is #2 of 3 with" in out
    moving = call(server, "share_of_search", brand="rising", competitors="brand a", timeframe="5y")
    assert row_for(moving, "rising (you)")[4].startswith("+")
    assert "at least one competitor" in call(server, "share_of_search", brand="brand a", competitors="brand a")


def test_content_calendar(server):
    out = call(server, "content_calendar", keywords="air conditioner, ski pass, brand a, rising")
    assert row_for(out, "air conditioner")[4] == "Jul" and row_for(out, "ski pass")[4] == "Jan"
    assert "## No yearly pattern" in out
    assert row_for(out, "brand a")[1] == "not seasonal" and row_for(out, "rising")[2] == "rising"


def test_daily_history_stitches_windows_and_finds_the_weekday_pattern(server):
    out = call(server, "daily_history", keyword="weekend", start="2025-01-01", end="2026-06-30", points=4)
    assert "546 days from 3 windows" in out and "Sat 156, Sun 156" in out and "Mon 78" in out
    assert len(rows(out)) == 5 and "Warning" not in out
    short = call(server, "daily_history", keyword="weekend", start="2026-05-01", end="2026-06-30", points=0)
    assert "61 days from 1 window" in short and "## Data" not in short
    assert "limit is 8" in call(server, "daily_history", keyword="weekend", start="2015-01-01", end="2026-06-30")


def test_daily_history_keeps_growth_across_windows(server):
    out = call(server, "daily_history", keyword="rising", start="2025-01-01", end="2026-06-30", points=4)
    values = [int(r[1]) for r in rows(out)[1:]]
    assert values == sorted(values) and values[-1] > values[0] * 1.2  # one scale: later windows are not reset to 100


def test_keyword_ideas_merges_spellings_and_seeds(server):
    out = call(server, "keyword_ideas", seeds="tesla, byd")
    rising, top = out.split("## Top")
    assert rows(rising)[1][0] == "Common  Query" and rows(rising)[1][2] == "tesla, byd"  # shared by both seeds: first
    assert row_for(rising, "tesla 2027")[1] == "Breakout" and row_for(rising, "tesla price")[3] == "in rising and top"
    assert len([r for r in rows(top) if "ommon" in r[0]]) == 1
    assert "no related queries" in call(server, "keyword_ideas", seeds="nothing")


# -- history ---------------------------------------------------------------------


def test_snapshot_and_trending_history(server):
    assert "No Trending Now history is saved yet" in call(server, "trending_history")
    first = call(server, "snapshot_trending", geos="US,GB,IR")
    assert row_for(first, "US")[1:] == ["5", "5", "0"]
    again = call(server, "snapshot_trending", geos="US")
    assert row_for(again, "US")[1:] == ["5", "0", "5"]  # nothing is duplicated

    everything = call(server, "trending_history")
    assert "9 trends, biggest first" in everything and "US since" in everything and "(2 snapshots)" in everything
    assert rows(everything)[1][0] == "champions league"
    assert [r[0] for r in rows(call(server, "trending_history", contains="Budget"))[1:]] == ["budget 2026"]
    assert [r[0] for r in rows(call(server, "trending_history", contains="apple", geo="US"))[1:]] == ["iphone 18"]
    assert [r[0] for r in rows(call(server, "trending_history", contains="ai"))[1:]] == ["ai news"]
    assert "No saved trend matches" in call(server, "trending_history", contains="zebra")
    assert "Trending history" in call(server, "get_capabilities")


def test_history_sql_is_read_only(server):
    call(server, "snapshot_trending", geos="US")
    out = call(server, "history_sql", query="SELECT geo, title, volume FROM trending WHERE geo='US' ORDER BY volume DESC LIMIT 2;")
    assert rows(out)[1] == ["US", "champions league", "500000"] and "2 rows" in out
    assert "Only SELECT" in call(server, "history_sql", query="DROP TABLE trending")
    assert "SQL error" in call(server, "history_sql", query="SELECT * FROM nope")
    assert "SQL error" in call(server, "history_sql", query="WITH x AS (SELECT 1) DELETE FROM trending")
    assert "returned no rows" in call(server, "history_sql", query="SELECT * FROM trending WHERE geo='ZZ'")


def test_watchlist(server):
    assert "watchlist is empty" in call(server, "watchlist_report")
    assert "Added 3 to the watchlist for United States (US)" in call(server, "watchlist_add", keywords="rising, fading, ai", geo="US", note="client x")
    assert "Added 0" in call(server, "watchlist_add", keywords="rising", geo="US") and "1 already there" in call(server, "watchlist_add", keywords="rising", geo="US")
    out = call(server, "watchlist_report")
    assert row_for(out, "rising")[2] == "rising" and row_for(out, "fading")[2] == "declining"
    assert "TRENDING in US: ai news (50K+)" in row_for(out, "ai")[7] and row_for(out, "fading")[7] == "client x"
    assert row_for(out, "rising")[6] == ""  # first reading: nothing to compare with

    # an earlier reading with a different verdict: the report says what changed.
    # (The reading just stored is removed first — otherwise the outcome depends on whether the two reports
    # fall in the same second, and the report would compare with it instead.)
    store = server.rt.store()
    db = store._db()
    db.execute("DELETE FROM readings WHERE keyword = 'rising'")
    db.commit()
    store.reading_add("rising", "US", 10, 10, -30.0, "declining", taken_at=1_700_000_000)
    assert row_for(call(server, "watchlist_report"), "rising")[6].startswith("was declining on 2023-11-")

    page = call(server, "watchlist_report", limit=2)
    assert "2 of 3 terms" in page and "1 more — call again with offset=2" in page
    assert "1 of 3 terms" in call(server, "watchlist_report", limit=2, offset=2)
    assert "Removed 1 watchlist entry. 2 left" in call(server, "watchlist_remove", keywords="fading")
    assert "empty for that location" in call(server, "watchlist_report", geo="DE")


# -- behaviour that holds for every tool -------------------------------------------


def test_text_from_google_cannot_act_as_markdown(server):
    from conftest import TRENDS

    TRENDS["US"].append(("evil | [click](http://x) <img>", 900000, 100, 1, None, ["evil | [click](http://x) <img>"], [11]))
    try:
        call(server, "clear_cache", what="trending")
        out = call(server, "trending_now", geo="US", limit=1)
    finally:
        TRENDS["US"].pop()
        call(server, "clear_cache", what="trending")
    assert "evil \\| \\[click\\](http://x) &lt;img>" in out


def test_a_rate_limit_is_an_error_with_advice_not_an_empty_result(server):
    call(server, "clear_cache", what="all")
    server.http.fail = [429] * 20
    try:
        out = call(server, "interest_over_time", keywords="tesla")
    finally:
        server.http.fail = []
        server.rt.client()._blocked.clear()
    assert out.startswith("Error:") and "rate-limiting" in out and "GTRENDS_PROXY" in out


def test_a_stale_answer_is_labelled(server):
    call(server, "clear_cache", what="all")
    client = server.rt.client()
    ttl = client.settings.trending_ttl
    object.__setattr__(client.settings, "trending_ttl", 0.001)
    try:
        call(server, "trending_now", geo="GB")
        import time

        time.sleep(0.01)
        server.http.fail = [429] * 20
        out = call(server, "trending_now", geo="GB")
    finally:
        server.http.fail = []
        client._blocked.clear()
        object.__setattr__(client.settings, "trending_ttl", ttl)
    assert "budget 2026" in out and "_Note: Google did not answer" in out and "showing the answer saved" in out


@pytest.mark.parametrize("tool", ["interest_over_time", "related_queries", "seasonality", "find_spikes"])
def test_unknown_location_is_rejected_before_any_request(server, tool):
    sent = len(server.http.calls)
    arg = "keywords" if tool == "interest_over_time" else "keyword"
    assert "not a location code" in call(server, tool, **{arg: "tesla", "geo": "United States"})
    assert len(server.http.calls) == sent


# -- found in review -----------------------------------------------------------------


def expire(server) -> None:
    db = server.rt.store()._db()
    db.execute("UPDATE cache SET expires = 0")
    db.commit()


def test_check_endpoints_reports_dead_endpoints_even_with_a_full_cache(server):
    assert "8 of 8 working" in call(server, "check_endpoints")
    server.http.fail = [404] * 100
    try:
        out = call(server, "check_endpoints")
    finally:
        server.http.fail = []
    assert "0 of 8 working" in out and out.count("FAILED") == 8


def test_snapshot_refuses_to_record_an_old_answer_as_new(server):
    call(server, "trending_now", geo="DE")
    expire(server)
    before = len(server.rt.store().trending_search("DE", None))
    server.http.fail = [404] * 20
    try:
        out = call(server, "snapshot_trending", geos="DE")
    finally:
        server.http.fail = []
    assert out.startswith("Error:") and "Nothing could be saved" in out
    assert len(server.rt.store().trending_search("DE", None)) == before


def test_compare_many_does_not_depend_on_the_first_term_having_interest(server):
    out = call(server, "compare_many", keywords="nothing, big, brand a, brand b")
    assert [r[1] for r in rows(out)[1:]] == ["big", "brand a", "brand b", "nothing"] and "Could not be placed" not in out
    assert "too little search volume" in call(server, "compare_many", keywords="nothing, nothing + nothing")


def test_compare_many_flags_everything_placed_through_an_imprecise_reference(server):
    # "tiny" is the anchor and is squeezed to ~1 beside "big" in the first group
    out = call(server, "compare_many", keywords="big, tesla, brand a, brand b, tiny, brand c, byd", anchor="tiny")
    flagged = {r[1] for r in rows(out)[1:] if r[5] == "≈"}
    assert flagged == {"brand c", "byd"} and "≈ marks terms" in out


def test_a_tool_that_runs_out_of_time_keeps_what_it_fetched(server):
    call(server, "clear_cache", what="all")
    settings = server.rt.client().settings
    server.rt.client()._slow.clear()  # earlier tests provoked refusals; start from the usual pace
    object.__setattr__(settings, "time_budget", 5.0)  # room for about three paced requests
    try:
        out = call(server, "trend_momentum", keywords="rising, fading, brand a, brand b, brand c")
        daily = call(server, "daily_history", keyword="weekend", start="2024-01-01", end="2026-06-30", points=0)
    finally:
        object.__setattr__(settings, "time_budget", 50.0)
    assert row_for(out, "rising")[1] == "rising" and "Not loaded" in out and "brand c" in out.split("Not loaded")[1]
    assert "the time for this call ran out" in out
    assert "from 1 window" in daily and "could not be loaded, so the series stops early" in daily


def test_watchlist_report_says_where_to_continue_after_an_interruption(server):
    store = server.rt.store()
    for w in store.watch_list():
        store.watch_remove(w["keyword"])
    call(server, "watchlist_add", keywords="rising, fading, brand a, brand b", geo="US")
    call(server, "clear_cache", what="all")
    settings = server.rt.client().settings
    server.rt.client()._slow.clear()
    object.__setattr__(settings, "time_budget", 7.0)
    try:
        out = call(server, "watchlist_report")
    finally:
        object.__setattr__(settings, "time_budget", 50.0)
        for w in store.watch_list():
            store.watch_remove(w["keyword"])
    measured = len(rows(out)) - 1
    assert 1 <= measured < 4 and "failed:" not in out
    assert f"Call again with offset={measured}" in out and f"{measured} of 4 terms" in out


def test_related_topics_is_explained_correctly_from_the_cache_after_a_restart(server):
    call(server, "related_topics", keyword="brand a")
    server.rt.client().user_type = None  # as after a restart: the client has not talked to Google yet
    out = call(server, "related_topics", keyword="brand a")
    assert "anonymous sessions" in out and "not evidence" in out


def test_hours_are_shown_as_used(server):
    assert "past 191 hours" in call(server, "trending_now", geo="US", hours=500)
    assert "past 1 hours" in call(server, "match_trends", terms="ai", geo="US", hours=0)


def test_merged_rows_of_hourly_data_carry_the_time_of_day(server):
    out = call(server, "interest_over_time", keywords="tesla", timeframe="7d", points=6)
    labels = [r[0] for r in rows(out) if r[0].startswith("from ")]
    assert len(labels) == 6 and all(re.fullmatch(r"from \d{4}-\d{2}-\d{2} \d{2}:\d{2}", lab) for lab in labels)
    assert len(set(labels)) == 6


def test_compare_periods_counts_in_the_unit_of_the_data(server):
    weekly = call(server, "compare_periods", keyword="rising", period="2025-10-01 2026-09-30", points=5)
    assert "| from day |" in weekly or "| from week |" in weekly
    daily = call(server, "compare_periods", keyword="rising", period="2026-06-01 2026-08-31", points=5)
    assert "| from day |" in daily and "consecutive days" in daily


def test_history_sql_limit_is_reported_as_applied(server):
    call(server, "snapshot_trending", geos="US")
    out = call(server, "history_sql", query="SELECT title FROM trending", limit=2)
    assert "2 rows — cut at the limit of 2; there are more." in out
    exact = call(server, "history_sql", query="SELECT title FROM trending ORDER BY title LIMIT 2", limit=2)
    assert exact.rstrip().endswith("2 rows.")  # exactly as many rows as the limit is not a cut
    assert "the limit" not in call(server, "history_sql", query="SELECT title FROM trending WHERE geo='US'", limit=5000)
