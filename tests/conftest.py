"""A fake Google Trends and a server wired to it — no network.

``FakeHTTP`` stands in for ``requests.Session`` and answers the same URLs
with the same wire formats as the real service (XSSI prefix, signed-token
widgets, batchexecute envelopes, the RSS feed). The data behind it is
synthetic and deterministic: each keyword has a known shape, so a test can
assert that the seasonal term peaks in July and the rising one is rising.
"""

import asyncio
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote

import pytest

XSSI = ")]}'\n"
NOW = int(datetime.now(timezone.utc).timestamp())

# title, volume, growth, started (hours ago), ended (hours ago or None), breakdown, category ids
TRENDS = {
    "US": [
        ("champions league", 500000, 1000, 5, None, ["champions league", "ucl draw", "real madrid"], [17]),
        ("iphone 18", 200000, 800, 20, 4, ["iphone 18", "iphone 18 price", "apple event"], [18]),
        ("ai news", 50000, 300, 2, None, ["ai news", "openai"], [18]),
        ("rain tomorrow", 20000, 200, 8, None, ["rain tomorrow", "weather"], [20]),
        ("café opening", 1000, 100, 30, 10, ["café opening"], [5]),
    ],
    "GB": [
        ("Champions League", 200000, 900, 6, None, ["champions league", "arsenal"], [17]),
        ("budget 2026", 100000, 500, 3, None, ["budget 2026"], [14]),
    ],
    "DE": [
        ("bundesliga", 100000, 400, 4, None, ["bundesliga", "champions league"], [17]),
    ],
    "IR": [
        ("قیمت دلار", 5000, 300, 3, None, ["قیمت دلار", "قیمت طلا"], [3]),
        ("فوتبال", 2000, 200, 9, None, ["فوتبال"], [17]),
    ],
}

GEO_TREE = {
    "name": "Worldwide",
    "id": "",
    "children": [
        {"name": "Iran", "id": "IR", "children": [{"name": "Tehran", "id": "23"}, {"name": "Isfahan", "id": "10"}]},
        {"name": "United States", "id": "US", "children": [{"name": "California", "id": "CA", "children": [{"name": "San Diego CA", "id": "825"}]}, {"name": "Texas", "id": "TX"}]},
        {"name": "United Kingdom", "id": "GB", "children": []},
        {"name": "Germany", "id": "DE", "children": []},
    ],
}
CATEGORY_TREE = {
    "name": "All categories",
    "id": 0,
    "children": [
        {"name": "Autos & Vehicles", "id": 47, "children": [{"name": "Vehicle Brands", "id": 815}]},
        {"name": "Finance", "id": 7, "children": []},
    ],
}
REGIONS = {"": [("United States", "US"), ("Germany", "DE"), ("Iran", "IR")], "US": [("California", "US-CA"), ("Texas", "US-TX"), ("Wyoming", "US-WY")], "IR": [("Tehran", "IR-23"), ("Isfahan", "IR-10")]}


def raw_value(keyword: str, d: date) -> float:
    """The made-up 'true' interest in a keyword on a day."""
    k = keyword.lower().strip()
    if " + " in k:
        return sum(raw_value(part, d) for part in k.split(" + "))
    doy = d.timetuple().tm_yday
    age = (date.today() - d).days
    if k in ("air conditioner", "sunscreen"):
        return 50 + 45 * math.cos(2 * math.pi * (doy - 196) / 365.25)  # peaks mid-July
    if k == "ski pass":
        return 50 + 45 * math.cos(2 * math.pi * (doy - 15) / 365.25)  # peaks mid-January
    if k == "rising":
        return max(5.0, 100 - age * 0.05)
    if k == "fading":
        return 20 + min(80.0, age * 0.045)
    if k == "spiky":
        return 100.0 if 196 <= age <= 203 else 20.0
    if k == "newcomer":
        return 60.0 if age < 200 else 0.0
    if k == "tiny":
        return 1.0
    if k == "nothing":
        return 0.0
    if k == "big":
        return 90.0
    if k == "weekend":
        return 80.0 if d.weekday() >= 5 else 40.0
    if k == "/m/0dr90d":
        return 70.0
    return {"brand a": 60.0, "brand b": 30.0, "brand c": 10.0, "tesla": 80.0, "byd": 8.0}.get(k, 40.0)


def time_points(time_value: str) -> tuple[list[datetime], str]:
    """The timestamps Google would return for a range, and its resolution."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if time_value.startswith("now"):
        n, unit = re.match(r"now (\d+)-(\w)", time_value).groups()
        hours = int(n) * (24 if unit == "d" else 1)
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)
        return [start + timedelta(hours=i) for i in range(hours + 1)], "HOUR"
    if time_value == "all":
        start, end = datetime(2004, 1, 1, tzinfo=timezone.utc), today
    elif time_value.startswith("today"):
        n, unit = re.match(r"today (\d+)-(\w)", time_value).groups()
        days = int(n) * (30 if unit == "m" else 365)
        start, end = today - timedelta(days=days), today
    else:
        a, b = time_value.split(" ")
        start = datetime.fromisoformat(a[:10]).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(b[:10]).replace(tzinfo=timezone.utc)
    days = (end - start).days + 1
    if days < 270:
        return [start + timedelta(days=i) for i in range(days)], "DAY"
    if days <= 1900:
        first = start - timedelta(days=(start.weekday() + 1) % 7)  # weeks start on Sunday
        return [first + timedelta(weeks=i) for i in range((end - first).days // 7 + 1)], "WEEK"
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(datetime(y, m, 1, tzinfo=timezone.utc))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out, "MONTH"


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, cookies=None):
        self.status_code, self.text, self.headers, self.cookies = status_code, text, headers or {}, cookies or {}


class FakeHTTP:
    """Answers like trends.google.com.

    ``fail`` queues statuses to return before real answers. ``require_cookie``
    makes the Explore endpoints refuse a request without a session cookie and
    hand one out, as Google does; ``challenge_new`` makes every request that
    carries that new cookie fail until ``mature`` is set; ``revoked`` names a
    cookie value that is refused and answered with a replacement.
    """

    def __init__(self):
        self.cookies = {}  # what a real session would have collected; the client must keep it empty
        self.calls: list[tuple[str, str]] = []
        self.sent_cookies: list[str | None] = []
        self.fail: list[int] = []
        self.user_type = "USER_TYPE_SCRAPER"
        self.tokens: dict[str, dict] = {}
        self.require_cookie = False
        self.challenge_new = False
        self.mature = False
        self.revoked: str | None = None  # a cookie value Google no longer honours: refused, and a new one issued

    def paths(self) -> list[str]:
        return [url.split("google.com")[1] for _, url in self.calls]

    def request(self, method, url, params=None, data=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append((method, url))
        self.followed_redirects = allow_redirects
        cookie = (headers or {}).get("Cookie")
        self.sent_cookies.append(cookie)
        self.cookies["NID"] = "library-jar"  # the client is expected to clear this after every request
        if self.fail:
            return FakeResponse(self.fail.pop(0), "<html>Error</html>")
        needs = "/trends/api/explore" == url.split("google.com")[1] or "/widgetdata/" in url
        if self.revoked and cookie == f"NID={self.revoked}":
            return FakeResponse(429, "<html>Error 429</html>", cookies={"NID": "fresh"})
        if self.challenge_new and cookie == "NID=fresh" and not self.mature:
            return FakeResponse(429, "<html>unusual traffic</html>")
        if self.require_cookie and needs and not cookie:
            return FakeResponse(429, "<html>Error 429</html>", cookies={"NID": "fresh"})
        resp = self._answer(url, params, data)
        if self.require_cookie and not cookie:
            resp.cookies = {"NID": "fresh"}
        return resp

    def _answer(self, url, params, data):
        params = params or {}
        path = url.split("google.com")[1]
        if path == "/trends/api/explore":
            return FakeResponse(200, XSSI + json.dumps(self._explore(json.loads(params["req"]))))
        if path.startswith("/trends/api/widgetdata/"):
            return FakeResponse(200, XSSI + json.dumps({"default": self._widget(path.rsplit("/", 1)[1], json.loads(params["req"]), params["token"])}))
        if path.startswith("/trends/api/autocomplete/"):
            text = unquote(path.rsplit("/", 1)[1])
            topics = [{"mid": "/m/0dr90d", "title": "Tesla", "type": "Automotive company"}, {"mid": "/m/036wfx", "title": "Tesla", "type": "Band"}] if text == "tesla" else []
            return FakeResponse(200, XSSI + json.dumps({"default": {"topics": topics}}))
        if path == "/trends/api/explore/pickers/geo":
            return FakeResponse(200, XSSI + json.dumps(GEO_TREE))
        if path == "/trends/api/explore/pickers/category":
            return FakeResponse(200, XSSI + json.dumps(CATEGORY_TREE))
        if path == "/_/TrendsUi/data/batchexecute":
            return FakeResponse(200, self._batch(data))
        if path == "/trending/rss":
            return FakeResponse(200, self._rss(params["geo"]))
        return FakeResponse(404, "<html>404</html>")

    # -- explore ---------------------------------------------------------------

    def _explore(self, req: dict) -> dict:
        items = req["comparisonItem"]
        if len(items) > 5 or any(i["geo"] == "ZZ" for i in items):
            raise AssertionError("the client should never send this")
        geos = {i["geo"] for i in items}
        times = {i["time"] for i in items}
        user = {"userType": self.user_type}
        widgets = []

        def add(wid, wtype, request):
            token = f"tok{len(self.tokens)}"
            self.tokens[token] = {"items": items, "req": req}
            widgets.append({"id": wid, "type": wtype, "token": token, "request": {**request, "userConfig": user}})

        if "" in geos and len(geos) > 1:
            widgets.append({"id": "worldwide_note", "type": "fe_text", "text": {"text": "Worldwide cannot be compared with countries"}})
        elif len(times) > 1:
            add("TIMESERIES", "fe_multi_range_chart", {"resolution": "DAY"})
        else:
            add("TIMESERIES", "fe_line_chart", {"resolution": time_points(items[0]["time"])[1]})
        if len(geos) == 1:
            add("GEO_MAP", "fe_multi_heat_map" if len(items) > 1 else "fe_geo_chart_explore", {"resolution": "COUNTRY" if items[0]["geo"] == "" else "REGION"})
        if len(items) == 1:
            add("RELATED_TOPICS", "fe_related_searches", {"keywordType": "ENTITY"})
            add("RELATED_QUERIES", "fe_related_searches", {"keywordType": "QUERY"})
        return {"widgets": widgets, "keywords": [{"keyword": i["keyword"], "name": "Tesla" if i["keyword"] == "/m/0dr90d" else i["keyword"], "type": "Search term"} for i in items]}

    def _widget(self, kind: str, request: dict, token: str) -> dict:
        items = self.tokens[token]["items"]
        if kind == "multiline":
            stamps, _ = time_points(items[0]["time"])
            raw = [[raw_value(i["keyword"], t.date()) for i in items] for t in stamps]
            top = max((v for row in raw for v in row), default=0) or 1
            rows = []
            for n, (t, row) in enumerate(zip(stamps, raw)):
                vals = [round(v / top * 100) for v in row]
                entry = {"time": str(int(t.timestamp())), "formattedTime": t.strftime("%b %-d, %Y") if os.name != "nt" else t.strftime("%b %d, %Y"), "value": vals, "hasData": [v > 0 for v in vals], "formattedValue": [str(v) for v in vals]}
                if n == len(stamps) - 1 and not re.match(r"\d{4}-", items[0]["time"]):
                    entry["isPartial"] = True
                rows.append(entry)
            return {"timelineData": rows, "averages": []}
        if kind == "multirange":
            series = []
            for i in items:
                stamps, _ = time_points(i["time"])
                series.append([(t, raw_value(i["keyword"], t.date())) for t in stamps])
            top = max((v for s in series for _, v in s), default=0) or 1
            rows = []
            for n in range(max(len(s) for s in series)):
                cols = []
                for s in series:
                    t, v = s[min(n, len(s) - 1)]
                    cols.append({"time": str(int(t.timestamp())), "formattedTime": t.strftime("%b %d, %Y"), "value": round(v / top * 100), "isPartial": False})
                rows.append({"columnData": cols})
            return {"timelineData": rows}
        if kind == "comparedgeo":
            places = REGIONS.get(items[0]["geo"], [])
            rows = []
            for n, (name, code) in enumerate(places):
                if len(items) == 1:
                    vals = [100 - n * 30]
                else:
                    share = [raw_value(i["keyword"], date.today()) + n * (5 if k else -5) for k, i in enumerate(items)]
                    total = sum(share) or 1
                    vals = [round(s / total * 100) for s in share]
                rows.append({"geoCode": code, "geoName": name, "value": vals, "hasData": [True] * len(vals)})
            if request.get("includeLowSearchVolumeGeos"):
                rows.append({"geoCode": "XX", "geoName": "Small Place", "value": [1] * len(items), "hasData": [True] * len(items)})
            if request.get("resolution") == "CITY":
                rows = [{"geoName": "Springfield", "coordinates": {"lat": 1.0, "lng": 2.0}, "value": [100], "hasData": [True]}]
            return {"geoMapData": rows}
        if kind == "relatedsearches":
            kw = items[0]["keyword"]
            if kw == "nothing":
                return {"rankedList": [{"rankedKeyword": []}, {"rankedKeyword": []}]}
            if request.get("keywordType") == "ENTITY":
                if self.user_type != "USER_TYPE_LEGIT_USER":
                    return {"rankedList": []}
                return {"rankedList": [{"rankedKeyword": [{"topic": {"mid": "/m/0dr90d", "title": "Tesla", "type": "Automotive company"}, "value": 100, "formattedValue": "100"}]}, {"rankedKeyword": []}]}
            top = [{"query": f"{kw} price", "value": 100, "formattedValue": "100"}, {"query": f"{kw} review", "value": 55, "formattedValue": "55"}, {"query": "common query", "value": 30, "formattedValue": "30"}]
            rising = [{"query": f"{kw} 2027", "value": 62300, "formattedValue": "Breakout"}, {"query": f"{kw} price", "value": 250, "formattedValue": "+250%"}, {"query": "Common  Query", "value": 120, "formattedValue": "+120%"}]
            return {"rankedList": [{"rankedKeyword": top}, {"rankedKeyword": rising}]}
        raise AssertionError(kind)

    # -- trending now ------------------------------------------------------------

    def _batch(self, data: str) -> str:
        outer = json.loads(parse_qs(data)["f.req"][0])
        rpc, payload = outer[0][0][0], json.loads(outer[0][0][1])
        if rpc == "i0OFE":
            geo = payload[2]
            rows = []
            for n, (title, volume, growth, start, end, breakdown, cats) in enumerate(TRENDS.get(geo, [])):
                rows.append([title, None, geo, [NOW - start * 3600], [NOW - end * 3600] if end else None, None, volume, None, growth, breakdown, cats, [[1000 + n, "en", geo]], title.lower()])
            inner = [None, rows]
        elif rpc == "w4opAf":
            inner = [[["Headline about it", "https://news.example/a", "Example News", [NOW - 3600], "https://img.example/a.jpg"]]]
        else:
            return ')]}\'\n\n[["er",null,null,null,null,400,null,null,null,3]]'
        return ")]}'\n\n123\n" + json.dumps([["wrb.fr", rpc, json.dumps(inner, ensure_ascii=False), None, None, None, "generic"]], ensure_ascii=False) + "\n25\n" + '[["di",21]]'

    def _rss(self, geo: str) -> str:
        items = "".join(
            f"<item><title>{title}</title><ht:approx_traffic>{volume // 1000}K+</ht:approx_traffic><pubDate>Thu, 1 Oct 2026 07:10:00 -0700</pubDate>"
            f"<ht:news_item><ht:news_item_title>News on {title}</ht:news_item_title><ht:news_item_url>https://news.example/{n}</ht:news_item_url>"
            f"<ht:news_item_source>Example News</ht:news_item_source></ht:news_item></item>"
            for n, (title, volume, *_rest) in enumerate(TRENDS.get(geo, []))
        )
        return f'<?xml version="1.0" encoding="UTF-8"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>{items}</channel></rss>'


class FakeClock:
    """Time that passes only when the client sleeps — so pacing and budgets behave as they do for real, instantly."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(tmp_path, http=None, trusted_session=True, **env):
    """A real TrendsClient over the fake HTTP layer, with no waiting."""
    from gtrends_mcp_full.client import TrendsClient
    from gtrends_mcp_full.settings import Settings
    from gtrends_mcp_full.store import Store

    keep = {k: os.environ.get(k) for k in env}
    os.environ["GTRENDS_CONFIG_DIR"] = str(tmp_path)
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        settings = Settings.from_env()
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    http = http or FakeHTTP()
    clock = FakeClock()
    client = TrendsClient(settings, Store(tmp_path / "t.sqlite"), http=http, sleep=clock.sleep, clock=clock)
    client.slept = clock.slept
    if trusted_session:
        trust(client)
    return client


def trust(client) -> None:
    """Give the client a session cookie Google already accepts, as after its first minutes of use."""
    client._cookie = {"value": "test", "issued": 0.0, "proven": True}
    client._cookie_loaded = True


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("cfg")
    for var in [v for v in os.environ if v.startswith("GTRENDS_")]:
        os.environ.pop(var)
    os.environ["GTRENDS_CONFIG_DIR"] = str(cfg)
    os.environ["GTRENDS_TIMEZONE"] = "UTC"
    from gtrends_mcp_full import server as srv
    from gtrends_mcp_full.client import TrendsClient
    from gtrends_mcp_full.settings import Settings
    from gtrends_mcp_full.store import Store

    srv.rt.settings = Settings.from_env()
    srv.rt._store = Store(cfg / "t.sqlite")
    srv.http = FakeHTTP()
    srv.clock = FakeClock()
    srv.rt._client = TrendsClient(srv.rt.settings, srv.rt._store, http=srv.http, sleep=srv.clock.sleep, clock=srv.clock)
    trust(srv.rt._client)
    return srv


def call(server, name, **args) -> str:
    """Run a tool; a tool error comes back as text starting with 'Error:' so tests can assert on it."""
    from gtrends_mcp_full.runtime import ToolError

    try:
        res = asyncio.run(server.mcp.call_tool(name, args))
    except ToolError as e:
        return f"Error: {e}"
    return "\n".join(getattr(c, "text", "") for c in res.content)
