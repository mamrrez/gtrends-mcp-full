"""Hostile input and malformed answers.

Two sweeps and the specific cases they turned up:

- every parameter of every tool is given empty, oversized, injected and
  out-of-range values;
- every tool is run against answers from Google that are empty, truncated,
  of the wrong type or absurdly large.

A tool may refuse (a tool error with a reason) or answer; it may never crash,
leak a Python error, echo an essay back, or put control characters in its output.
"""

import copy
import json
import re

import pytest

from conftest import NOW, XSSI, FakeResponse, call

BASE = {
    "get_capabilities": {},
    "check_endpoints": {},
    "find_location": {"query": "iran"},
    "find_category": {"query": "vehicle"},
    "find_topic": {"text": "tesla"},
    "clear_cache": {"what": "expired"},
    "trending_now": {"geo": "US"},
    "trend_details": {"trend": "iphone", "geo": "US"},
    "trending_feed": {"geo": "US"},
    "trending_across_countries": {"geos": "US,GB"},
    "match_trends": {"terms": "ai", "geo": "US"},
    "interest_over_time": {"keywords": "tesla, byd"},
    "interest_by_region": {"keywords": "tesla, byd", "geo": "US"},
    "related_queries": {"keyword": "tesla"},
    "related_topics": {"keyword": "tesla"},
    "compare_periods": {"keyword": "tesla", "period": "2026-06-01 2026-08-31"},
    "compare_locations": {"keyword": "tesla", "geos": "US,DE"},
    "keyword_overview": {"keyword": "tesla"},
    "compare_many": {"keywords": "tesla, byd, big, brand a, brand b, brand c, tiny"},
    "seasonality": {"keyword": "air conditioner"},
    "trend_momentum": {"keywords": "rising"},
    "find_spikes": {"keyword": "spiky"},
    "share_of_search": {"brand": "brand a", "competitors": "brand b"},
    "content_calendar": {"keywords": "ski pass"},
    "daily_history": {"keyword": "weekend", "start": "2025-01-01", "end": "2026-03-01"},
    "keyword_ideas": {"seeds": "tesla"},
    "snapshot_trending": {"geos": "US"},
    "trending_history": {},
    "watchlist_add": {"keywords": "tesla"},
    "watchlist_remove": {"keywords": "zzz"},
    "watchlist_report": {},
    "history_sql": {"query": "SELECT 1"},
}

STRINGS = [
    "",
    "   ",
    "x" * 5000,
    "a," * 200,
    "\x00",
    "\n\n",
    "| a | b |\n|---|",
    "[x](javascript:alert(1))",
    "<script>alert(1)</script>",
    "'; DROP TABLE trending; --",
    "😀🔥",
    "‮abc",
    "-1",
    "null",
    "%s%s%s",
    "../../etc/passwd",
    'a + b - c "d"',
    "/m/",
    ",,,",
]
INTEGERS = [0, -1, 1, 10**9, -(10**9)]
NUMBERS = [0.0, -1.0, 1e9, float("nan"), float("inf")]

# What a leaked Python error looks like, whatever it was wrapped in.
LEAK = re.compile(
    r"Unexpected |Not found:|empty sequence|invalid literal|could not convert|unpack|has no attribute|math domain|division by zero"
    r"|NoneType|list index|out of range|Traceback|object is not|must be str|unsupported operand|not supported between"
)
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")


def problems(out: str) -> list[str]:
    found = []
    if LEAK.search(out):
        found.append("leaks a Python error")
    if CONTROL.search(out):
        found.append("control characters in the output")
    if len(out) > 20_000:
        found.append(f"{len(out):,} characters of output")
    return found


def tool_schemas(server) -> dict:
    return {t.name: (t.parameters or {}).get("properties", {}) for t in server.mcp._tool_manager.list_tools()}


def settle(server) -> None:
    server.rt.client()._blocked.clear()
    server.rt.client()._slow.clear()
    server.http.fail = []


def test_the_sweeps_cover_every_tool(server):
    assert set(tool_schemas(server)) == set(BASE)


@pytest.mark.parametrize("tool", sorted(BASE))
def test_no_parameter_value_makes_a_tool_crash(server, tool):
    failures = []
    for name, schema in tool_schemas(server)[tool].items():
        kind = schema.get("type")
        values = STRINGS if kind == "string" else INTEGERS if kind == "integer" else NUMBERS if kind == "number" else [True, False]
        for value in values:
            settle(server)
            out = call(server, tool, **{**BASE[tool], name: value})
            for problem in problems(out):
                failures.append(f"{name}={value!r:.40}: {problem}: {out[:160]!r}")
    assert not failures, "\n".join(failures)


# -- malformed answers ---------------------------------------------------------------


def timeline(change):
    def apply(http):
        real = http._widget

        def widget(kind, request, token):
            data = real(kind, request, token)
            return change(copy.deepcopy(data)) if kind in ("multiline", "multirange") else data

        http._widget = widget

    return apply


def other_widget(which, change):
    def apply(http):
        real = http._widget
        http._widget = lambda kind, request, token: change(copy.deepcopy(real(kind, request, token))) if kind == which else real(kind, request, token)

    return apply


def each_point(set_value):
    def change(data):
        for row in data["timelineData"]:
            if "value" in row:
                set_value(row, len(row["value"]))
            else:
                for col in row["columnData"]:
                    set_value(col, None)
        return data

    return change


def values(v):
    return each_point(lambda row, n: row.update(value=v if n is None else [v] * n))


def trending_rows(change):
    def apply(http):
        real = http._batch

        def batch(data):
            text = real(data)
            outer = json.loads(next(line for line in text.splitlines() if line.startswith("[[")))
            if outer[0][1] != "i0OFE":
                return text
            outer[0][2] = json.dumps(change(json.loads(outer[0][2])), ensure_ascii=False)
            return ")]}'\n\n" + json.dumps(outer, ensure_ascii=False)

        http._batch = batch

    return apply


def each_trend(change):
    def apply(inner):
        for row in inner[1]:
            change(row)
        return inner

    return apply


def explore(change):
    def apply(http):
        real = http._explore
        http._explore = lambda req: change(real(req))

    return apply


def answers(make):
    def apply(http):
        real = http._answer

        def answer(url, params, data):
            response = make(url)
            return response if response is not None else real(url, params, data)

        http._answer = answer

    return apply


def body_for(fragment, text):
    return answers(lambda url: FakeResponse(200, text) if fragment in url else None)


def drop(keys):
    return each_point(lambda row, n: [row.pop(k, None) for k in keys])


def short_rows(data):
    for row in data["timelineData"]:
        if "value" in row:
            row["value"], row["hasData"] = row["value"][:1], row.get("hasData", [True])[:1]
        else:
            row["columnData"] = row["columnData"][:1]
    return data


def keep(n):
    def change(data):
        data["timelineData"] = data["timelineData"][:n]
        return data

    return change


MALFORMED = {
    "timeline: no points": timeline(keep(0)),
    "timeline: one point": timeline(keep(1)),
    "timeline: two points": timeline(keep(2)),
    "timeline: key missing": timeline(lambda data: {}),
    "timeline: all zero": timeline(values(0)),
    "timeline: values null": timeline(values(None)),
    "timeline: values are text": timeline(values("abc")),
    "timeline: values negative": timeline(values(-5)),
    "timeline: values enormous": timeline(values(10**12)),
    "timeline: fewer values than terms": timeline(short_rows),
    "timeline: no times or labels": timeline(drop(("time", "formattedTime", "hasData"))),
    "timeline: no labels": timeline(drop(("formattedTime",))),
    "timeline: every point partial": timeline(each_point(lambda row, n: row.update(isPartial=True))),
    "regions: none": other_widget("comparedgeo", lambda d: {"geoMapData": []}),
    "regions: key missing": other_widget("comparedgeo", lambda d: {}),
    "regions: fields missing": other_widget("comparedgeo", lambda d: {"geoMapData": [{"geoName": "X"}, {}, None, {"value": []}, {"value": [None], "geoName": None}]}),
    "regions: fewer values than terms": other_widget("comparedgeo", lambda d: {"geoMapData": [{"geoCode": "A", "geoName": "A", "value": [5], "hasData": [True]}]}),
    "related: key missing": other_widget("relatedsearches", lambda d: {}),
    "related: one list only": other_widget("relatedsearches", lambda d: {"rankedList": d.get("rankedList", [])[:1]}),
    "related: fields missing": other_widget("relatedsearches", lambda d: {"rankedList": [{"rankedKeyword": [{}, {"query": None, "value": None}, {"topic": None}]}, {}]}),
    "related: enormous": other_widget(
        "relatedsearches",
        lambda d: {
            "rankedList": [
                {"rankedKeyword": [{"query": f"q{i} " + "y" * 600, "value": 100 - i % 100} for i in range(500)]},
                {"rankedKeyword": [{"query": f"r{i}", "value": 10**9} for i in range(500)]},
            ]
        },
    ),
    "explore: no charts": explore(lambda d: {"widgets": [], "keywords": []}),
    "explore: charts null": explore(lambda d: {"widgets": None}),
    "explore: no names": explore(lambda d: {"widgets": d["widgets"]}),
    "explore: names malformed": explore(lambda d: {**d, "keywords": [None, {}]}),
    "trending: none": trending_rows(lambda inner: [None, []]),
    "trending: rows null": trending_rows(lambda inner: [None, None]),
    "trending: rows cut short": trending_rows(lambda inner: [None, [row[:5] for row in inner[1]]]),
    "trending: fields null": trending_rows(each_trend(lambda row: [row.__setitem__(i, None) for i in (3, 4, 6, 8, 9, 10, 11)])),
    "trending: fields of the wrong type": trending_rows(
        each_trend(lambda row: [row.__setitem__(i, v) for i, v in ((3, ["soon"]), (6, "many"), (8, "lots"), (9, "not a list"), (10, [999, None, "x"]))])
    ),
    "trending: titles null": trending_rows(each_trend(lambda row: row.__setitem__(0, None))),
    "trending: times in the future and the distant past": trending_rows(each_trend(lambda row: (row.__setitem__(3, [NOW + 10**7]), row.__setitem__(4, [1])))),
    "trending: three thousand trends": trending_rows(
        lambda inner: [
            None,
            [[f"trend {i}", None, "US", [NOW - i], None, None, 100 * (i % 50), None, i, [f"q{i}-{j}" for j in range(80)], [17], [[i, "en", "US"]], ""] for i in range(3000)],
        ]
    ),
    "feed: empty": lambda http: setattr(http, "_rss", lambda geo: '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'),
    "feed: not XML": lambda http: setattr(http, "_rss", lambda geo: "<rss><channel><item><title>x</item>"),
    "feed: items without content": lambda http: setattr(
        http,
        "_rss",
        lambda geo: '<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item></item><item><title/><pubDate>never</pubDate><ht:news_item/></item></channel></rss>',
    ),
    "topics: entries malformed": body_for("/autocomplete/", XSSI + '{"default": {"topics": [null, {}, {"mid": null}]}}'),
    "topics: no list": body_for("/autocomplete/", XSSI + "{}"),
    "locations: nodes malformed": body_for("pickers/geo", XSSI + '{"children": [{"name": null}, null, {"id": "US", "name": "United States", "children": null}, {"id": 5}]}'),
    "categories: nodes malformed": body_for("pickers/category", XSSI + '{"children": [{"name": null, "id": null}, null, {"id": "x", "name": "Y"}]}'),
    "everything: an HTML page": lambda http: setattr(http, "_answer", lambda url, params, data: FakeResponse(200, "<html>consent page</html>")),
    "everything: an empty body": lambda http: setattr(http, "_answer", lambda url, params, data: FakeResponse(200, "")),
    "everything: JSON null": lambda http: setattr(http, "_answer", lambda url, params, data: FakeResponse(200, XSSI + "null")),
    "everything: a JSON list": lambda http: setattr(http, "_answer", lambda url, params, data: FakeResponse(200, XSSI + "[]")),
    "everything: a JSON string": lambda http: setattr(http, "_answer", lambda url, params, data: FakeResponse(200, XSSI + '"sorry"')),
}


@pytest.fixture
def odd(server):
    """Run one tool against one malformed answer, on an empty cache, and put the fake back afterwards."""
    http = server.http
    originals = {name: getattr(http, name) for name in ("_widget", "_explore", "_batch", "_rss", "_answer")}

    def run(mutation: str, tool: str, **args) -> str:
        for name, fn in originals.items():
            setattr(http, name, fn)
        db = server.rt.store()._db()
        db.execute("DELETE FROM cache")
        db.commit()
        settle(server)
        MALFORMED[mutation](http)
        return call(server, tool, **(args or BASE[tool]))

    yield run
    for name, fn in originals.items():
        setattr(http, name, fn)
    db = server.rt.store()._db()
    db.execute("DELETE FROM cache")
    db.commit()
    settle(server)


@pytest.mark.parametrize("mutation", sorted(MALFORMED))
def test_no_malformed_answer_makes_a_tool_crash(server, odd, mutation):
    call(server, "watchlist_add", keywords="rising, tesla", geo="US")
    failures = []
    for tool in sorted(BASE):
        out = odd(mutation, tool)
        for problem in problems(out):
            failures.append(f"{tool}: {problem}: {out[:160]!r}")
    assert not failures, "\n".join(failures)


# -- what the sweeps turned up: input ---------------------------------------------------


def test_a_long_input_is_not_echoed_back_in_full(server):
    for tool, arg in (("find_location", "query"), ("find_topic", "text"), ("trending_now", "geo"), ("trending_now", "category"), ("interest_over_time", "timeframe"), ("compare_many", "anchor")):
        out = call(server, tool, **{**BASE[tool], arg: "x" * 5000})
        assert len(out) < 700, (tool, arg, len(out))


def test_control_characters_never_reach_the_output(server):
    out = call(server, "find_location", query="ir\x00an‮")
    assert "\x00" not in out and "‮" not in out and "ir an" in out


def test_a_negative_or_absurd_category_is_refused_before_google_is_asked(server):
    sent = len(server.http.calls)
    for tool in ("interest_over_time", "interest_by_region", "related_queries", "seasonality", "compare_many", "keyword_overview"):
        assert "is not a category id" in call(server, tool, **{**BASE[tool], "category": -1}), tool
        assert "is not a category id" in call(server, tool, **{**BASE[tool], "category": 10**9}), tool
    assert len(server.http.calls) == sent


def test_numeric_options_are_kept_in_range(server):
    assert "threshold must be a number from 1.2 to 1000" in call(server, "find_spikes", keyword="spiky", threshold=float("nan"))
    assert "threshold must be a number from 1.2 to 1000" in call(server, "find_spikes", keyword="spiky", threshold=float("inf"))
    assert "lead_weeks must be between 0 and 26" in call(server, "content_calendar", keywords="ski pass", lead_weeks=-1)
    assert "lead_weeks must be between 0 and 26" in call(server, "seasonality", keyword="air conditioner", lead_weeks=10**9)
    call(server, "snapshot_trending", geos="US")
    assert "past 1 days" in call(server, "trending_history", days=-5) and "past 3650 days" in call(server, "trending_history", days=10**9)


def test_a_blank_time_range_means_the_tools_own_default(server):
    assert "past 5 years" in call(server, "seasonality", keyword="air conditioner", timeframe="")
    assert "past 5 years" in call(server, "trend_momentum", keywords="rising", timeframe="  ")
    assert "past 5 years" in call(server, "find_spikes", keyword="spiky", timeframe="")
    assert "past 12 months" in call(server, "interest_over_time", keywords="tesla", timeframe="")


def test_an_empty_search_asks_for_one(server):
    assert "Give part of a category name" in call(server, "find_category", query="  ")
    assert "At most 1 keywords" in call(server, "compare_many", keywords="a, b, c, d, e, f", anchor="x, y")


# -- what the sweeps turned up: answers ---------------------------------------------------


def test_a_row_with_fewer_values_than_terms_is_padded_not_fatal(odd):
    out = odd("timeline: fewer values than terms", "share_of_search", brand="brand a", competitors="brand b")
    assert "brand a (you)" in out and "| brand b | 0.0% |" in out
    assert "# Interest over time" in odd("timeline: fewer values than terms", "interest_over_time")


def test_values_off_the_scale_are_held_to_it(odd):
    out = odd("timeline: values enormous", "interest_over_time", keywords="tesla", points=0)
    assert "| tesla | 100 | 100 " in out and "1000000000000" not in out
    assert "too little search volume" in odd("timeline: values negative", "interest_over_time")
    assert "too little search volume" in odd("timeline: values are text", "keyword_overview")


def test_points_without_a_label_get_one_and_points_without_a_time_are_dropped(odd):
    out = odd("timeline: no labels", "interest_over_time", keywords="tesla", points=0)
    assert re.search(r"\| tesla \| 100 \| 100 \(\d{4}-\d{2}-\d{2}\) \|", out)
    assert "too little search volume" in odd("timeline: no times or labels", "interest_over_time")


def test_too_few_points_is_said_plainly(odd):
    assert "Only 1 data point came back" in odd("timeline: one point", "find_spikes")
    assert "| rising | too little data |" in odd("timeline: two points", "trend_momentum")
    assert "At least two full years" in odd("timeline: one point", "seasonality")
    assert "returned no daily data" in odd("timeline: no points", "daily_history")


def test_a_series_that_is_all_partial_still_has_a_shape(odd):
    out = odd("timeline: every point partial", "interest_over_time", keywords="tesla", points=0)
    assert "▄" in out


def test_rows_without_the_essentials_are_left_out(odd):
    assert "No place has enough search volume" in odd("regions: fields missing", "interest_by_region")
    assert "no related queries" in odd("related: fields missing", "related_queries")
    assert "format this version does not understand" in odd("trending: titles null", "trending_now")  # nothing usable at all
    assert "feed for United States (US) is empty" in odd("feed: items without content", "trending_feed")
    assert "knows no topic" in odd("topics: entries malformed", "find_topic")
    assert "Nothing found" in odd("locations: nodes malformed", "find_location", query="zz")
    assert "| US | United States |" in odd("locations: nodes malformed", "find_location", query="united")


def test_a_trend_without_times_or_numbers_is_shown_without_inventing_them(odd):
    out = odd("trending: fields null", "trending_now")
    assert "| 1 | champions league | — | — | — | active |" in out and " d |" not in out


def test_an_answer_in_an_unknown_format_is_named_as_such(odd):
    assert "format this version does not understand" in odd("trending: fields of the wrong type", "trending_now")
    assert "not JSON" in odd("everything: an HTML page", "interest_over_time")
    assert "format this version does not understand" in odd("everything: a JSON list", "find_location")
    assert "no time chart for this request; the endpoint may have changed" in odd("explore: no charts", "interest_over_time")
    assert "not valid XML" in odd("feed: not XML", "trending_feed")


def test_an_enormous_answer_stays_a_readable_size(odd):
    assert len(odd("related: enormous", "keyword_ideas")) < 20_000
    assert len(odd("trending: three thousand trends", "trending_now")) < 6_000
    assert "3,000 trends" in odd("trending: three thousand trends", "trending_now")
    assert "| US | 3000 |" in odd("trending: three thousand trends", "snapshot_trending")
