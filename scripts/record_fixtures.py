#!/usr/bin/env python3
"""Record one real answer from every Google Trends endpoint into tests/fixtures/.

The tests run against a hand-written fake. These recordings are what keep the
fake honest: tests/test_contract.py replays them through the real parser, so a
change in Google's wire format shows up as a failing test after the next
recording instead of as a broken tool.

Run it rarely and on purpose — it sends about 15 paced requests to Google:

    python scripts/record_fixtures.py

Bodies are stored as Google sent them, with three changes: signed tokens are
replaced by a placeholder, the long lists (trends, locations, categories) are
cut to a sample, and nothing but the body is kept (no cookies, no headers).
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gtrends_mcp_full.client import RPC_TREND_NEWS, RPC_TRENDING_NOW, TrendsClient  # noqa: E402
from gtrends_mcp_full.settings import Settings  # noqa: E402
from gtrends_mcp_full.store import Store  # noqa: E402

OUT = ROOT / "tests" / "fixtures"
PREFIX = ")]}'\n"


class Recorder:
    """Wraps the client's HTTP session and keeps every body, in order."""

    def __init__(self, http):
        self.http = http
        self.bodies: list[tuple[str, str]] = []
        self.cookies = http.cookies

    def request(self, method, url, **kwargs):
        resp = self.http.request(method, url, **kwargs)
        if resp.status_code == 200:
            self.bodies.append((url, resp.text))
        return resp


def unwrap(text: str):
    start = min(i for i in (text.find("{"), text.find("[")) if i >= 0)
    return json.loads(text[start:])


def save(name: str, body: str) -> None:
    (OUT / name).write_text(body, encoding="utf-8")
    print(f"  {name:<34} {len(body):>8,} bytes")


def save_json(name: str, data) -> None:
    save(name, PREFIX + json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def scrub_tokens(explore: dict) -> dict:
    for w in explore.get("widgets", []):
        if "token" in w:
            w["token"] = "RECORDED"
    return explore


def cut_tree(tree: dict, keep: set[str]) -> dict:
    """The first few entries plus the named ones, so the sample stays small but real."""
    children = tree.get("children", [])
    return {**tree, "children": [c for i, c in enumerate(children) if i < 3 or str(c.get("id")) in keep]}


def batch_payload(text: str, rpc: str):
    for line in text.splitlines():
        if line.startswith("[["):
            outer = json.loads(line)
            if outer[0][1] == rpc:
                return outer, json.loads(outer[0][2])
    raise SystemExit(f"no {rpc} payload in the answer")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    client = TrendsClient(settings, Store(settings.db_path))
    rec = Recorder(client._session())
    client._http = rec
    client.begin_call(600.0)
    ready = client.session_ready_in()
    if ready:
        print(f"new session: waiting {ready:.0f}s for the cookie to mature")

    def last(fragment: str) -> str:
        return next(body for url, body in reversed(rec.bodies) if fragment in url)

    one = {"keyword": "coffee", "geo": "US", "time": "today 12-m"}
    two = [one, {**one, "keyword": "tea"}]
    end = date.today() - timedelta(days=30)
    ranges = [
        {"keyword": "coffee", "geo": "US", "time": f"{end - timedelta(days=29)} {end}"},
        {"keyword": "coffee", "geo": "US", "time": f"{end - timedelta(days=394)} {end - timedelta(days=365)}"},
    ]

    print("recording …")
    client.interest_over_time([one], fresh=True)
    save_json("explore_one.json", scrub_tokens(unwrap(last("/api/explore"))))
    save("multiline_one.json", last("/widgetdata/multiline"))
    client.interest_by_region([one])
    save("comparedgeo_one.json", last("/widgetdata/comparedgeo"))
    client.related(one, "queries")
    save("related_queries.json", last("/widgetdata/relatedsearches"))
    client.related(one, "topics")
    save("related_topics.json", last("/widgetdata/relatedsearches"))

    client.interest_over_time(two, fresh=True)
    save_json("explore_two.json", scrub_tokens(unwrap(last("/api/explore"))))
    save("multiline_two.json", last("/widgetdata/multiline"))
    client.interest_by_region(two)
    save("comparedgeo_two.json", last("/widgetdata/comparedgeo"))

    client.interest_over_time(ranges, fresh=True)
    save_json("explore_ranges.json", scrub_tokens(unwrap(last("/api/explore"))))
    save("multirange.json", last("/widgetdata/multirange"))

    trends = client.trending_now("US", 24, fresh=True)
    outer, inner = batch_payload(last("batchexecute"), RPC_TRENDING_NOW)
    inner[1] = inner[1][:15]
    outer[0][2] = json.dumps(inner, ensure_ascii=False)
    save("trending_now.txt", ")]}'\n\n" + json.dumps(outer, ensure_ascii=False))
    tokens = next(t["news_tokens"] for t in trends if t["news_tokens"])
    client.trend_news(tokens, 3)
    batch_payload(last("batchexecute"), RPC_TREND_NEWS)
    save("trend_news.txt", last("batchexecute"))

    client.trending_rss("US", fresh=True)
    save("trending_rss.xml", last("/trending/rss"))
    client.autocomplete("coffee", fresh=True)
    save("autocomplete.json", last("/autocomplete/"))
    client.geo_tree(fresh=True)
    save_json("geo.json", cut_tree(unwrap(last("pickers/geo")), {"US", "IR", "GB", "DE"}))
    client.category_tree(fresh=True)
    save_json("category.json", cut_tree(unwrap(last("pickers/category")), {"47"}))

    manifest = {
        "recorded": date.today().isoformat(),
        "requests": client.requests_sent,
        "session": client.user_type,
        "hl": settings.hl,
        "questions": {"one": one, "two": two, "ranges": ranges, "trending": "US, 24 hours", "topic lookup": "coffee"},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"done: {client.requests_sent} requests, session {client.user_type}")


if __name__ == "__main__":
    main()
