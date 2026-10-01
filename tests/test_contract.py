"""Recorded answers from Google, replayed through the real parser.

The rest of the suite runs against a hand-written fake. These tests are what
keeps that fake honest: the files in ``tests/fixtures`` are real bodies
recorded by ``scripts/record_fixtures.py``, and here they go through the same
client code as live traffic. Record again, and a change in Google's wire
format fails here instead of in someone's conversation.
"""

import json
from pathlib import Path

import pytest

from conftest import FakeClock, FakeHTTP, FakeResponse, trust
from gtrends_mcp_full.client import TrendsClient
from gtrends_mcp_full.geo import geo_children, search_categories, search_geo, walk_geo
from gtrends_mcp_full.settings import Settings
from gtrends_mcp_full.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
ONE = MANIFEST["questions"]["one"]
TWO = MANIFEST["questions"]["two"]
RANGES = MANIFEST["questions"]["ranges"]


def body(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parsed(name: str):
    text = body(name)
    return json.loads(text[min(i for i in (text.find("{"), text.find("[")) if i >= 0) :])


class ReplayHTTP:
    """Answers each endpoint with the body Google really sent for it."""

    def __init__(self):
        self.cookies = {}
        self.calls: list[str] = []

    def request(self, method, url, params=None, data=None, headers=None, timeout=None, allow_redirects=True):
        path = url.split("google.com")[1]
        self.calls.append(path)
        params = params or {}
        req = json.loads(params["req"]) if "req" in params else {}
        if path == "/trends/api/explore":
            items = req["comparisonItem"]
            name = "explore_ranges.json" if len({i["time"] for i in items}) > 1 else "explore_two.json" if len(items) > 1 else "explore_one.json"
        elif path.endswith("/multiline"):
            name = "multiline_two.json" if len(req["comparisonItem"]) > 1 else "multiline_one.json"
        elif path.endswith("/multirange"):
            name = "multirange.json"
        elif path.endswith("/comparedgeo"):
            name = "comparedgeo_two.json" if len(req["comparisonItem"]) > 1 else "comparedgeo_one.json"
        elif path.endswith("/relatedsearches"):
            name = "related_topics.json" if req.get("keywordType") == "ENTITY" else "related_queries.json"
        elif path == "/_/TrendsUi/data/batchexecute":
            name = "trending_now.txt" if params.get("rpcids") == "i0OFE" else "trend_news.txt"
        elif path == "/trending/rss":
            name = "trending_rss.xml"
        elif "/autocomplete/" in path:
            name = "autocomplete.json"
        elif path.endswith("/pickers/geo"):
            name = "geo.json"
        elif path.endswith("/pickers/category"):
            name = "category.json"
        else:
            return FakeResponse(404, "")
        return FakeResponse(200, body(name))


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in [v for v in __import__("os").environ if v.startswith("GTRENDS_")]:
        monkeypatch.delenv(var)
    monkeypatch.setenv("GTRENDS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GTRENDS_TIMEZONE", "UTC")
    clock = FakeClock()
    c = TrendsClient(Settings.from_env(), Store(tmp_path / "t.sqlite"), http=ReplayHTTP(), sleep=clock.sleep, clock=clock)
    trust(c)
    c.begin_call(600.0)
    return c


def test_the_recording_is_complete_and_holds_no_secrets():
    names = {p.name for p in FIXTURES.iterdir()}
    assert names >= {
        "manifest.json", "explore_one.json", "explore_two.json", "explore_ranges.json", "multiline_one.json", "multiline_two.json",
        "multirange.json", "comparedgeo_one.json", "comparedgeo_two.json", "related_queries.json", "related_topics.json",
        "trending_now.txt", "trend_news.txt", "trending_rss.xml", "autocomplete.json", "geo.json", "category.json",
    }  # fmt: skip
    for name in ("explore_one.json", "explore_two.json", "explore_ranges.json"):
        tokens = {w["token"] for w in parsed(name)["widgets"] if "token" in w}
        assert tokens == {"RECORDED"}, name  # the signed tokens are not kept
    for p in FIXTURES.iterdir():
        text = p.read_text(encoding="utf-8")
        assert "NID=" not in text and "Set-Cookie" not in text, p.name


def test_one_term_over_time(client):
    data = client.interest_over_time([ONE])
    raw = parsed("multiline_one.json")["default"]["timelineData"]
    assert data["labels"] == ["coffee"] and data["resolution"] == "WEEK" and not data["multirange"]
    assert len(data["points"]) == len(raw) >= 52
    assert [p["values"] for p in data["points"]] == [[float(r["value"][0])] for r in raw]
    assert all(0 <= p["values"][0] <= 100 for p in data["points"]) and max(p["values"][0] for p in data["points"]) == 100
    assert [p["t"] for p in data["points"]] == sorted(int(r["time"]) for r in raw)
    assert all(p["label"] for p in data["points"])
    assert [p["partial"] for p in data["points"]] == [bool(r.get("isPartial")) for r in raw]
    assert client.user_type == "USER_TYPE_SCRAPER"


def test_two_terms_share_one_scale(client):
    data = client.interest_over_time(TWO)
    assert data["labels"] == ["coffee", "tea"] and all(len(p["values"]) == 2 for p in data["points"])
    assert max(v for p in data["points"] for v in p["values"]) == 100
    assert data["averages"] == parsed("multiline_two.json")["default"]["averages"]


def test_two_time_ranges_side_by_side(client):
    data = client.interest_over_time(RANGES)
    assert data["multirange"] and data["resolution"] == "DAY" and len(data["points"]) >= 28
    first = data["points"][0]
    assert len(first["t"]) == len(first["values"]) == len(first["label"]) == 2
    assert first["t"][0] - first["t"][1] == 365 * 86400  # the same day, a year apart


def test_interest_by_region(client):
    one = client.interest_by_region([ONE])
    assert one["resolution"] == "REGION" and len(one["rows"]) == 51
    assert all(r["code"].startswith("US-") and r["name"] and len(r["values"]) == 1 for r in one["rows"])
    assert max(r["values"][0] for r in one["rows"]) == 100
    two = client.interest_by_region(TWO)
    assert all(len(r["values"]) == 2 and abs(sum(r["values"]) - 100) <= 1 for r in two["rows"])  # each place splits 100% between the terms


def test_related_queries(client):
    rel = client.related(ONE, "queries")
    raw = parsed("related_queries.json")["default"]["rankedList"]
    assert [e["text"] for e in rel["top"]] == [r["query"] for r in raw[0]["rankedKeyword"]]
    assert rel["top"][0]["value"] == 100 and all(e["growth"] == "" and not e["breakout"] for e in rel["top"])
    assert len(rel["rising"]) == len(raw[1]["rankedKeyword"])
    for mine, theirs in zip(rel["rising"], raw[1]["rankedKeyword"]):
        # the number decides, and it agrees with the word Google prints
        assert mine["breakout"] == (theirs["formattedValue"] == "Breakout"), theirs
        assert mine["growth"] == theirs["formattedValue"], theirs


def test_related_topics_come_back_empty_for_an_anonymous_session(client):
    rel = client.related(ONE, "topics")
    assert rel["top"] == [] and rel["rising"] == [] and rel["available"] is True and rel["user_type"] == "USER_TYPE_SCRAPER"


def test_trending_now(client):
    trends = client.trending_now("US", 24)
    assert len(trends) == 15
    for t in trends:
        assert t["title"] and t["geo"] == "US" and t["started"] > 1_700_000_000
        assert t["ended"] is None or t["ended"] >= t["started"]
        assert t["volume"] >= 100 and t["growth"] >= 0
        assert t["title"] in t["breakdown"] or t["breakdown"] == [] or all(isinstance(q, str) for q in t["breakdown"])
        assert t["categories"] and not any(c.startswith("Category ") for c in t["categories"])  # every category id is known
        assert isinstance(t["news_tokens"], list)


def test_trend_news(client):
    news = client.trend_news([[1, "en", "US"]], 3)
    assert news and all(a["title"] and a["url"].startswith("http") and a["source"] and a["time"] > 1_700_000_000 for a in news)


def test_trending_feed(client):
    items = client.trending_rss("US")
    assert len(items) >= 5
    assert all(i["title"] and i["traffic"].endswith("+") and i["started"] > 1_700_000_000 for i in items)
    assert any(i["news"] and i["news"][0]["url"].startswith("http") and i["news"][0]["source"] for i in items)


def test_topic_lookup(client):
    topics = client.autocomplete("coffee")
    assert topics[0] == {"mid": "/m/02vqfm", "title": "Coffee", "type": "Beverage"}
    assert all(t["mid"].startswith(("/m/", "/g/")) for t in topics)


def test_location_and_category_trees(client):
    tree = client.geo_tree()
    codes = {g["code"]: g for g in walk_geo(tree)}
    assert codes["US"]["name"] == "United States" and codes["US-CA"]["path"] == "United States › California"
    assert any(c.startswith("US-CA-") and c.split("-")[2].isdigit() for c in codes)  # metro areas sit under their state
    assert len(geo_children(tree, "IR")) == 31 and search_geo(tree, "tehran")[0]["code"].startswith("IR-")
    cats = client.category_tree()
    assert search_categories(cats, "autos")[0] == {"id": 47, "name": "Autos & Vehicles", "path": "Autos & Vehicles", "depth": 0}


# -- the fake against the recording -----------------------------------------------------


def keys_of(rows) -> set:
    return {k for r in rows for k in r}


def test_the_fake_only_uses_fields_that_google_really_sends():
    fake = FakeHTTP()
    explore_one = fake._explore({"comparisonItem": [ONE], "category": 0, "property": ""})
    explore_two = fake._explore({"comparisonItem": TWO, "category": 0, "property": ""})
    explore_ranges = fake._explore({"comparisonItem": RANGES, "category": 0, "property": ""})

    def widgets(data):
        return [(w["id"], w.get("type")) for w in data["widgets"] if w.get("type") != "fe_text"]

    def real_widgets(name):
        return [(w["id"], w.get("type")) for w in parsed(name)["widgets"] if w.get("type") != "fe_text"]

    assert widgets(explore_one) == real_widgets("explore_one.json")
    assert set(widgets(explore_two)) <= set(real_widgets("explore_two.json"))
    assert widgets(explore_ranges)[0] == real_widgets("explore_ranges.json")[0]
    assert set(explore_one) <= set(parsed("explore_one.json"))

    def token(data, wid):
        return next(w["token"] for w in data["widgets"] if w["id"] == wid)

    pairs = [
        (fake._widget("multiline", {}, token(explore_one, "TIMESERIES"))["timelineData"], parsed("multiline_one.json")["default"]["timelineData"]),
        (fake._widget("comparedgeo", {}, token(explore_one, "GEO_MAP"))["geoMapData"], parsed("comparedgeo_one.json")["default"]["geoMapData"]),
        (
            [e for lst in fake._widget("relatedsearches", {"keywordType": "QUERY"}, token(explore_one, "RELATED_QUERIES"))["rankedList"] for e in lst["rankedKeyword"]],
            [e for lst in parsed("related_queries.json")["default"]["rankedList"] for e in lst["rankedKeyword"]],
        ),
        (
            [c for row in fake._widget("multirange", {}, token(explore_ranges, "TIMESERIES"))["timelineData"] for c in row["columnData"]],
            [c for row in parsed("multirange.json")["default"]["timelineData"] for c in row["columnData"]],
        ),
    ]
    for fake_rows, real_rows in pairs:
        assert keys_of(fake_rows) <= keys_of(real_rows), keys_of(fake_rows) - keys_of(real_rows)


def test_the_fake_shapes_trending_rows_like_google():
    from urllib.parse import quote

    def rows(text):
        line = next(x for x in text.splitlines() if x.startswith("[["))
        return json.loads(json.loads(line)[0][2])[1]

    real = rows(body("trending_now.txt"))
    request = "f.req=" + quote(json.dumps([[["i0OFE", json.dumps([None, None, "US", 0, "en", 24, 1]), None, "generic"]]]))
    fake = rows(FakeHTTP()._batch(request))
    assert {len(r) for r in real} == {len(r) for r in fake} == {13}
    for position in range(13):
        seen = {type(r[position]).__name__ for r in real}
        if position == 4:
            seen |= {"list", "NoneType"}  # the end time: a list once the trend is over, null while it runs
        made = {type(r[position]).__name__ for r in fake}
        assert made <= seen, (position, made, seen)
