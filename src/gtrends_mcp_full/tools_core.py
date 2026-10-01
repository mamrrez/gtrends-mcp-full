"""Core tools: lookups, Trending Now, and the Explore charts."""

from __future__ import annotations

import time
from collections import Counter

from . import __version__
from . import analysis as an
from .client import MAX_COMPARE, PROPERTY_LABELS, TREND_CATEGORIES, TrendsError
from .format import (
    clip,
    explore_url,
    fmt_change,
    fmt_duration,
    fmt_index,
    fmt_num,
    fmt_time,
    fmt_volume,
    kv,
    local_dt,
    md_table,
    safe,
    safe_url,
    short_path,
    trending_url,
)
from .geo import geo_children, search_categories, search_geo
from .matching import TermMatcher, fold
from .runtime import Runtime, hints
from .timeframes import parse_timeframe, previous_period, year_before

READ = hints()
LOCAL_READ = hints(open_world=False)
LOCAL_WRITE = hints(read_only=False, open_world=False)  # changes only files on this machine

SCALE_NOTE = "_Values are Google's 0-100 index: 100 is the highest point among everything in this one request, not a search count._"


def _tool_names(mcp) -> list[str]:
    try:
        return sorted(t.name for t in mcp._tool_manager.list_tools())
    except Exception:  # SDK internals moved; capabilities must still answer
        return []


SUB_DAILY = {"MINUTE", "EIGHT_MINUTE", "SIXTEEN_MINUTE", "HOUR"}


def series_table(data: dict, labels: list[str], max_points: int, tz=None) -> str:
    """The data points of a series as a table, merged down to ``max_points`` rows.

    Points still being collected are never averaged into a row: they would drag it down.
    """
    complete = [p for p in data["points"] if not p.get("partial")]
    partial = [p for p in data["points"] if p.get("partial")]
    points, size = an.downsample(complete, max_points)
    rows = []
    for p in points:
        if size > 1:  # a merged row is named by the moment it starts at; Google's own label covers one point only
            if data.get("resolution") in SUB_DAILY:
                label = "from " + local_dt(p["t"], tz).strftime("%Y-%m-%d %H:%M")
            else:
                label = "from " + an.point_date(p).isoformat()
        else:
            label = p["label"] if isinstance(p["label"], str) else p["label"][0]
        rows.append([label] + [fmt_index(v) for v in p["values"]])
    for p in partial[-1:]:
        rows.append([(p["label"] if isinstance(p["label"], str) else p["label"][0]) + " *"] + [fmt_index(v) for v in p["values"]])
    out = md_table(["period"] + [safe(lab) for lab in labels], rows)
    notes = []
    if size > 1:
        notes.append(f"each row is the mean of the {size} points from that moment on ({len(data['points'])} in all; raise `points` for every one)")
    if partial:
        notes.append("* still being collected, so lower than it will end up")
    return out + (f"\n\n_{'; '.join(notes)}._" if notes else "")


def summary_rows(data: dict, labels: list[str]) -> list[list]:
    rows = []
    for i, lab in enumerate(labels):
        s = an.summarize(data["points"], i)
        if not s["has_data"]:
            rows.append([lab, "—", "no measurable interest", "—", "—", ""])
            continue
        m = an.momentum(data["points"], i)
        rows.append(
            [
                lab,
                fmt_index(s["average"]),
                f"{fmt_index(s['peak'])} ({s['peak_label']})",
                fmt_index(s["latest"]),
                fmt_change(m["change"]),
                an.sparkline(an.column(data["points"], i, complete_only=True)),
            ]
        )
    return rows


SUMMARY_HEADERS = ["term", "average", "peak (when)", "latest", "recent vs before", "shape"]


def resolution_words(code: str) -> str:
    return {
        "MINUTE": "one point per minute", "EIGHT_MINUTE": "one point per 8 minutes", "SIXTEEN_MINUTE": "one point per 16 minutes",
        "HOUR": "hourly points", "DAY": "daily points", "WEEK": "weekly points", "MONTH": "monthly points",
    }.get(code, code.lower() or "points")  # fmt: skip


def register(mcp, rt: Runtime) -> None:  # noqa: C901 - one registration function is clearer than 20 modules
    guarded = rt.guard

    # -- utilities -----------------------------------------------------------

    @mcp.tool(annotations=LOCAL_READ)
    @guarded
    def get_capabilities() -> str:
        """Version, settings, session and cache state, saved history and the list of tools. Call this first when unsure."""
        st, client, store = rt.settings, rt.client(), rt.store()
        blocked = client.blocked_for()
        lines = [f"# gtrends-mcp-full {__version__}", "", "No API key and no sign-in are needed.", "", "## Settings"]
        lines.append(
            kv(
                [
                    ("default location", rt.settings.geo or "Worldwide (set GTRENDS_GEO to change)"),
                    ("language of names", st.hl),
                    ("timezone", st.timezone or f"this machine's ({st.tz_label()})"),
                    ("proxy", "set" if st.proxy else "none"),
                    ("signed-in cookie", (f"NOT IN USE — {client.cookie_problem}" if client.cookie_problem else "set") if (st.cookie or st.cookies_file) else "none (anonymous session)"),
                    ("seconds between requests", f"{st.min_interval:g}"),
                    ("offline mode", "ON — answers come from the cache only" if st.offline else "off"),
                    ("data file", short_path(st.db_path)),
                ]
            )
        )
        lines += ["", "## Session"]
        lines.append(
            kv(
                [
                    ("how Google sees it", client.session_kind()),
                    ("requests sent since start", client.requests_sent),
                    ("answers served from cache", client.cache_hits),
                    ("refused by Google (HTTP 429) since start", client.refusals),
                    ("pace", "slowed after a refusal: charts ×{:.1f}".format(client.pace_factor("explore")) if client.pace_factor("explore") > 1 else "normal"),
                    ("backing off", f"yes — no new requests for {fmt_duration(blocked)}" if blocked else "no"),
                ]
            )
        )
        try:
            cs = store.cache_stats()
            lines += ["", "## Cache", f"{cs['entries']:,} saved answers ({cs['fresh']:,} still fresh), {cs['bytes'] / 1024:,.0f} KB."]
            cov = store.snapshot_coverage()
            if cov:
                tz = rt.tz()
                lines += ["", "## Trending history", md_table(["location", "snapshots", "first", "last", "trends saved"], [[c["geo"], c["snapshots"], fmt_time(c["first"], tz), fmt_time(c["last"], tz), fmt_num(c["trends"])] for c in cov])]
            else:
                lines.append("\nNo Trending Now history yet — snapshot_trending saves it (Google keeps a trend for about a week).")
            watched = store.watch_list()
            lines.append(f"\nWatchlist: {len(watched)} keyword{'s' if len(watched) != 1 else ''}.")
        except Exception as e:  # the store must never break capabilities
            lines.append(f"\nData file unavailable: {e}")
        lines += ["", "## Tools", ", ".join(_tool_names(mcp)) or "(ask your client to list the tools)"]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def check_endpoints() -> str:
        """Ask each Google Trends endpoint one small question and report which ones answer.

        Google Trends has no public API; this server uses the endpoints of the Trends website,
        which can change without notice. Run this when a tool fails unexpectedly: it separates
        "Google is rate-limiting me" from "this endpoint changed" from "the network is down".
        Sends about 12 requests and never answers from the cache.
        """
        client = rt.client()
        client.begin_call(max(rt.settings.time_budget, 90.0))
        results = client.check()
        ok = sum(1 for r in results if r[1])
        lines = [f"# Endpoint check — {ok} of {len(results)} working", "", md_table(["endpoint", "status", "detail"], [[n, "OK" if good else "FAILED", d] for n, good, d in results])]
        lines.append(f"\nSession as Google sees it: {client.session_kind()}.")
        if ok < len(results):
            lines.append("A 429 means Google is rate-limiting this connection: wait, or set GTRENDS_PROXY. A 404 or a parsing failure means the endpoint changed: update the package.")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def find_location(query: str = "", inside: str = "", limit: int = 25) -> str:
        """Find the location code (geo) for a country, region, state or metro area by name.

        query: part of a name ("tehran", "calif", "bavaria") or a code ("IR").
        inside: a code whose sub-locations to list instead ("IR" → its provinces, "US-CA" → its metro areas).
        Codes look like IR, US, US-CA, US-CA-807. Every other tool takes them as `geo`; blank means worldwide.
        """
        tree = rt.client().geo_tree()
        if inside.strip():
            code = rt.geo(inside)
            hits = geo_children(tree, code)
            title = f"# Locations inside {rt.geo_label(code)}"
            if query.strip():
                needle = query.strip().casefold()
                hits = [h for h in hits if needle in h["name"].casefold()]
        elif query.strip():
            hits = search_geo(tree, query, limit=max(1, limit))
            title = f"# Locations matching “{safe(clip(query.strip()))}”"
        else:
            raise ValueError("Give a name to search for, or a code in `inside` to list what it contains.")
        if not hits:
            return f"{title}\n\nNothing found. Try the English name, or a shorter part of it."
        # A list of what is inside a place is only useful whole (a country has up to about 80 regions); a search is capped.
        cap = max(limit, 100) if inside.strip() else max(1, limit)
        return f"{title}\n\n" + md_table(["code", "name", "where"], [[h["code"], h["name"], h["path"]] for h in hits], max_rows=cap)

    @mcp.tool(annotations=READ)
    @guarded
    def find_category(query: str, limit: int = 25) -> str:
        """Find the id of a Google Trends category ("Autos & Vehicles" = 47) to narrow a term to one meaning.

        A category limits a search term to searches Google files under that subject — "jaguar" in
        Autos & Vehicles is the car, in Pets & Animals the cat. Pass the id as `category` to the explore tools.
        With no keyword at all, a category id shows interest in the whole subject.
        """
        if not query.strip():
            raise ValueError("Give part of a category name to search for, e.g. “vehicle”, “finance”, “travel”.")
        hits = search_categories(rt.client().category_tree(), query, limit=max(1, limit))
        if not hits:
            return f"No category matches “{safe(clip(query.strip()))}”. Categories are named in English, e.g. “vehicle”, “finance”, “travel”."
        return f"# Categories matching “{safe(clip(query.strip()))}”\n\n" + md_table(["id", "name", "where"], [[h["id"], h["name"], h["path"]] for h in hits])

    @mcp.tool(annotations=READ)
    @guarded
    def find_topic(text: str) -> str:
        """Find the Google Trends *topics* for a piece of text and the ids that select them.

        A search term ("tesla") counts only searches containing that exact wording, in one language.
        A topic ("Tesla — Automotive company", id /m/0dr90d) counts every search about the thing,
        in any language and spelling, and leaves out other meanings (the band, the inventor).
        Pass a topic id wherever a keyword is expected.
        """
        topics = rt.client().autocomplete(text)
        if not topics:
            return f"Google Trends knows no topic for “{safe(clip(text.strip()))}”. It can still be measured as a plain search term."
        rows = [[t.get("title", ""), t.get("type", ""), t.get("mid", "")] for t in topics]
        return (
            f"# Topics for “{safe(clip(text.strip()))}”\n\n"
            + md_table(["topic", "what it is", "id to use as the keyword"], rows)
            + "\n\nA topic and a search term are measured differently and should not be compared with each other."
        )

    @mcp.tool(annotations=LOCAL_WRITE)
    @guarded
    def clear_cache(what: str = "expired", reset_session: bool = False) -> str:
        """Delete saved answers so the next call asks Google again.

        what: "expired" (default — only answers too old to be used even as a fallback), "all", or one kind:
        trending, timeseries, regions, related, explore.
        reset_session: also forget the stored session cookie and any back-off, for a clean start after a block.
        Saved Trending Now history and the watchlist are never touched.
        """
        key = what.strip().lower()
        kinds = {"trending", "timeseries", "regions", "related", "explore", "rss", "news", "autocomplete", "geo", "category"}
        if key in ("expired", ""):
            n = rt.store().cache_clear(only_expired=True)
        elif key == "all":
            n = rt.store().cache_clear()
        elif key in kinds:
            n = rt.store().cache_clear(kind=key)
        else:
            raise ValueError("what must be expired, all, or one of: " + ", ".join(sorted(kinds)))
        msg = f"Removed {n:,} saved answer{'s' if n != 1 else ''}."
        if reset_session:
            rt.client().forget_session()
            msg += " Session cookie and back-off state cleared."
        return msg

    # -- trending now ----------------------------------------------------------

    def _trend_status(t: dict, now: float) -> str:
        if not t["started"]:  # Google gave no start time: say what is known and no more
            return "ended" if t["ended"] else "active"
        if t["ended"]:
            return f"ended · lasted {fmt_duration(t['ended'] - t['started'])}"
        return f"active · {fmt_duration(now - t['started'])}"

    def _trend_row(i: int, t: dict, tz, now: float) -> list:
        others = [q for q in t["breakdown"] if q != t["title"]]
        also = ", ".join(others[:3]) + (f" +{len(others) - 3}" if len(others) > 3 else "")
        growth = f"+{t['growth']:,}%" if t["growth"] else "—"
        return [i, t["title"], fmt_volume(t["volume"]), growth, fmt_time(t["started"], tz), _trend_status(t, now), ", ".join(t["categories"]), also]

    TREND_HEADERS = ["#", "trend", "searches", "growth", "started", "status", "category", "also searched"]

    @mcp.tool(annotations=READ)
    @guarded
    def trending_now(
        geo: str = "",
        hours: int = 24,
        category: str = "",
        status: str = "all",
        sort_by: str = "volume",
        min_volume: int = 0,
        contains: str = "",
        limit: int = 25,
    ) -> str:
        """What people are searching right now: the full Trending Now list for a country or region.

        Each trend has its search volume, growth, start time, whether it is still active, its
        category and the other queries people type for the same story.

        geo: country or region code (US, GB, DE, IR, US-CA). Blank = the default location, else US.
        hours: look-back window — 4, 24, 48 or 168 (any value 1-191).
        category: filter by name, e.g. "sports", "technology", "business" (see the header of the result).
        status: "active" (still trending), "ended" or "all".
        sort_by: "volume", "growth" or "recent".
        contains: keep trends whose title or queries contain this text, as whole words.
        """
        code, hours = rt.country(geo), rt.hours(hours)
        trends = rt.client().trending_now(code, hours)
        tz, now = rt.tz(), time.time()
        total, active = len(trends), sum(1 for t in trends if not t["ended"])
        mix = Counter(c for t in trends for c in t["categories"])

        status_key = status.strip().lower()
        if status_key not in ("all", "active", "ended"):
            raise ValueError("status must be all, active or ended")
        if status_key == "active":
            trends = [t for t in trends if not t["ended"]]
        elif status_key == "ended":
            trends = [t for t in trends if t["ended"]]
        if category.strip():
            needle = category.strip().casefold()
            known = [c for c in TREND_CATEGORIES.values() if needle in c.casefold()]
            if not known:
                raise ValueError(f"No Trending Now category matches {clip(category.strip(), 40)!r}. Categories: " + ", ".join(sorted(TREND_CATEGORIES.values())))
            trends = [t for t in trends if any(c in known for c in t["categories"])]
        if min_volume:
            trends = [t for t in trends if t["volume"] >= min_volume]
        if contains.strip():
            matcher = TermMatcher([contains])
            trends = [t for t in trends if any(matcher.matches(q) for q in [t["title"], *t["breakdown"]])]

        sort_key = sort_by.strip().lower()
        if sort_key == "volume":
            trends.sort(key=lambda t: (-t["volume"], -t["growth"], -t["started"]))
        elif sort_key == "growth":
            trends.sort(key=lambda t: (-t["growth"], -t["volume"]))
        elif sort_key == "recent":
            trends.sort(key=lambda t: -t["started"])
        else:
            raise ValueError("sort_by must be volume, growth or recent")

        head = [f"# Trending now — {rt.geo_label(code)}, past {hours} hours", "", f"{total:,} trends, {active:,} still active."]
        if mix:
            head.append("By category: " + ", ".join(f"{c} {n}" for c, n in mix.most_common(8)) + ".")
        if not trends:
            return "\n".join(head) + ("\n\nNo trend matches the filters." if total else "\n\nGoogle lists no trends for this location. Trending Now covers about 125 countries; try the country instead of a region.")
        rows = [_trend_row(i, t, tz, now) for i, t in enumerate(trends, 1)]
        return "\n".join(head) + "\n\n" + md_table(TREND_HEADERS, rows, max_rows=max(1, limit)) + f"\n\n_Searches are Google's rounded-down buckets. Times are {rt.settings.tz_label()}._ [Open in Google Trends]({trending_url(code, hours)})"

    @mcp.tool(annotations=READ)
    @guarded
    def trend_details(trend: str, geo: str = "", hours: int = 48, news: int = 5, with_chart: bool = True) -> str:
        """Everything about one current trend: every query in it, the news behind it, and its hour-by-hour curve.

        trend: the trend's title, or any words it contains.
        geo: country or region code; blank = the default location, else US.
        with_chart: also fetch the last 7 days of hourly interest for the title (two more requests).
        """
        code, hours = rt.country(geo), rt.hours(hours)
        client = rt.client()
        trends = client.trending_now(code, hours)
        key, matcher = fold(trend), TermMatcher([trend])
        if not key:
            raise ValueError("Give the trend's title or a part of it.")
        exact = [t for t in trends if fold(t["title"]) == key]
        partial = [t for t in trends if any(matcher.matches(q) for q in [t["title"], *t["breakdown"]])]
        found = exact or partial
        if not found:
            return f"No trend in {rt.geo_label(code)} over the past {hours} hours matches “{safe(clip(trend.strip()))}”. Call trending_now to see the list, or raise `hours` (up to 191)."
        found.sort(key=lambda t: -t["volume"])
        t = found[0]
        tz, now = rt.tz(), time.time()
        lines = [f"# {safe(t['title'])} — trending in {rt.geo_label(code)}", ""]
        lines.append(
            kv(
                [
                    ("searches", fmt_volume(t["volume"])),
                    ("growth", f"+{t['growth']:,}%"),
                    ("started", fmt_time(t["started"], tz)),
                    ("status", _trend_status(t, now) + (f" · ended {fmt_time(t['ended'], tz)}" if t["ended"] else "")),
                    ("category", ", ".join(t["categories"]) or "—"),
                ]
            )
        )
        if t["breakdown"]:
            lines += ["", f"## Queries in this trend ({len(t['breakdown'])})", ", ".join(safe(q) for q in t["breakdown"][:60]) + (" …" if len(t["breakdown"]) > 60 else "")]
        if news > 0 and t["news_tokens"]:
            try:
                articles = client.trend_news(t["news_tokens"], min(news, 10))
            except TrendsError as e:
                articles = []
                lines += ["", f"_News could not be loaded: {e}_"]
            if articles:
                lines += ["", "## News", md_table(["headline", "source", "published", "link"], [[a["title"], a["source"], fmt_time(a["time"], tz), safe_url(a["url"])] for a in articles])]
        if with_chart:
            try:
                data = client.interest_over_time([{"keyword": t["title"], "geo": code.split("-")[0] if code.count("-") > 1 else code, "time": "now 7-d"}])
                vals = an.column(data["points"], 0)
                if vals and max(vals) > 0:
                    s = an.summarize(data["points"], 0)
                    lines += ["", "## Past 7 days, hour by hour", f"`{an.sparkline(vals, 56)}`", f"Peak {safe(s['peak_label'])}; latest complete hour at {fmt_index(s['latest'])} of 100."]
            except TrendsError as e:
                lines += ["", f"_The 7-day curve could not be loaded: {e}_"]
        if len(found) > 1:
            lines += ["", "Other matches: " + ", ".join(safe(x["title"]) for x in found[1:8]) + "."]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def trending_feed(geo: str = "", limit: int = 10) -> str:
        """The top daily search trends with the news stories behind each — Google's public trending feed.

        Lighter than trending_now (about 10-20 trends, one request) and it carries headlines and
        links, which makes it the quickest answer to "what is in the news in <country> today".
        geo: country code; blank = the default location, else US.
        """
        code = rt.country(geo)
        items = rt.client().trending_rss(code)
        if not items:
            return f"The trending feed for {rt.geo_label(code)} is empty right now."
        tz = rt.tz()
        lines = [f"# Daily search trends — {rt.geo_label(code)}", ""]
        for i, it in enumerate(items[: max(1, limit)], 1):
            lines.append(f"**{i}. {safe(it['title'])}** — {safe(it['traffic'] or '?')} searches · {fmt_time(it['started'], tz)}")
            for n in it["news"][:3]:
                lines.append(f"   - {safe(n['title'])} ({safe(n['source'])}) {safe_url(n['url'])}")
        if len(items) > limit:
            lines.append(f"\n_{len(items) - limit} more not shown; raise `limit`._")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def trending_across_countries(geos: str = "", hours: int = 24, min_countries: int = 2, limit: int = 30) -> str:
        """Trends that are live in several countries at once — the stories that travel.

        Pulls Trending Now for each country and groups trends that are the same search (the same
        title, or one country's title appearing among another's queries).

        geos: comma-separated country codes, up to 12. Blank = US, GB, CA, AU, IN, DE, FR, BR.
        min_countries: only show trends found in at least this many of them.
        """
        codes = rt.geos(geos, ["US", "GB", "CA", "AU", "IN", "DE", "FR", "BR"], cap=12)
        hours = rt.hours(hours)
        client = rt.client()
        per_geo: dict[str, list[dict]] = {}
        failed: list[str] = []
        for code in codes:
            try:
                per_geo[code] = client.trending_now(code, hours)
            except TrendsError as e:
                failed.append(f"{code} ({str(e).split('. ')[0]})")
        if not per_geo:
            raise TrendsError("None of the countries could be loaded: " + "; ".join(failed))

        groups: dict[str, dict] = {}
        alias: dict[str, str] = {}  # any query's key -> the group it belongs to
        ordered = sorted(((code, t) for code, ts in per_geo.items() for t in ts), key=lambda x: -x[1]["volume"])
        for code, t in ordered:
            tkey = fold(t["title"])
            # Same story when the title is one of a group's queries, or a group's title is one of this trend's queries.
            gid = alias.get(tkey) or next((k for k in (fold(q) for q in t["breakdown"][:25]) if k in groups), None)
            if gid is None:
                gid = tkey
                groups[gid] = {"title": t["title"], "geos": {}, "categories": Counter()}
            g = groups[gid]
            if code not in g["geos"] or t["volume"] > g["geos"][code]["volume"]:
                g["geos"][code] = t
            g["categories"].update(t["categories"])
            alias.setdefault(tkey, gid)
            for q in t["breakdown"][:25]:
                alias.setdefault(fold(q), gid)

        shared = [g for g in groups.values() if len(g["geos"]) >= max(1, min_countries)]
        shared.sort(key=lambda g: (-len(g["geos"]), -sum(t["volume"] for t in g["geos"].values())))
        lines = [f"# Trending across {len(per_geo)} countries, past {hours} hours", "", "Countries: " + ", ".join(f"{c} ({len(per_geo[c])})" for c in per_geo) + "."]
        if failed:
            lines.append("Not loaded: " + "; ".join(failed) + ".")
        if not shared:
            return "\n".join(lines) + f"\n\nNo trend is shared by {min_countries} or more of these countries right now."
        rows = []
        for g in shared[: max(1, limit)]:
            by_vol = sorted(g["geos"].items(), key=lambda kv_: -kv_[1]["volume"])
            rows.append(
                [
                    g["title"],
                    len(g["geos"]),
                    ", ".join(f"{c} {fmt_volume(t['volume'])}" for c, t in by_vol),
                    sum(1 for t in g["geos"].values() if not t["ended"]),
                    ", ".join(c for c, _ in g["categories"].most_common(2)),
                ]
            )
        return "\n".join(lines) + "\n\n" + md_table(["trend", "countries", "where · searches", "still active in", "category"], rows)

    @mcp.tool(annotations=READ)
    @guarded
    def match_trends(terms: str, geo: str = "", hours: int = 48, limit: int = 25) -> str:
        """Which current trends touch your subjects — for newsjacking and timely content.

        terms: comma-separated words or phrases that define your niche (brands, products, people,
        subjects). A trend matches when its title or any query inside it contains one of them as
        whole words, in any letter case: "ai" finds «ai news», not «rain».
        geo: country or region code; blank = the default location, else US.
        """
        matcher = TermMatcher(rt.keywords(terms, cap=50))
        if not matcher:
            raise ValueError("None of the terms has any letters or digits in it.")
        code, hours = rt.country(geo), rt.hours(hours)
        trends = rt.client().trending_now(code, hours)
        tz, now = rt.tz(), time.time()
        rows = []
        for t in sorted(trends, key=lambda t: -t["volume"]):
            hits = [(q, matcher.matching_terms(q)) for q in dict.fromkeys([t["title"], *t["breakdown"]])]
            hit_queries = [q for q, found in hits if found]
            if not hit_queries:
                continue
            hit_terms = list(dict.fromkeys(term for _, found in hits for term in found))
            status = f"ended {fmt_time(t['ended'], tz)}" if t["ended"] else f"active · {fmt_duration(now - t['started'])}"
            rows.append([t["title"], ", ".join(hit_terms), fmt_volume(t["volume"]), f"+{t['growth']:,}%", status, ", ".join(hit_queries[:4]) + (f" +{len(hit_queries) - 4}" if len(hit_queries) > 4 else "")])
        head = f"# Trends matching your terms — {rt.geo_label(code)}, past {hours} hours\n\nChecked {len(trends):,} trends against {len(matcher.terms)} term{'s' if len(matcher.terms) != 1 else ''}."
        if not rows:
            return head + "\n\nNone of them touches these terms right now. snapshot_trending on a schedule plus trending_history will catch the ones you miss."
        return head + "\n\n" + md_table(["trend", "your term", "searches", "growth", "status", "matching queries"], rows, max_rows=max(1, limit)) + "\n\ntrend_details shows the news and every query for one of them."

    # -- explore -----------------------------------------------------------------

    @mcp.tool(annotations=READ)
    @guarded
    def interest_over_time(
        keywords: str = "",
        timeframe: str = "12m",
        geo: str = "",
        category: int = 0,
        property: str = "web",
        points: int = 26,
    ) -> str:
        """Search interest over time for up to 5 terms on one shared scale — the main Google Trends chart.

        keywords: comma-separated terms or topic ids (find_topic). Operators work inside a term:
        `a + b` either wording, `a -b` excluding b, `"a b"` exact phrase. Blank with a `category`
        measures the whole category.
        timeframe: 1h, 4h, 1d, 7d, 1m, 3m, 12m, 5y, all; any 6m / 2y / 45d; a year 2024; a month
        2024-03; or explicit dates "2024-01-01 2024-12-31". Short ranges come back by the minute
        or hour, up to ~9 months daily, up to 5 years weekly, longer monthly.
        geo: location code (find_location); blank = default location, usually worldwide.
        category: category id (find_category) to pin down an ambiguous term.
        property: web, youtube, news, images or shopping.
        points: rows of data to return (longer series are averaged down); 0 = summary only.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        if keywords.strip():
            kws = rt.keywords(keywords, cap=MAX_COMPARE)
        elif category:
            kws = [""]
        else:
            raise ValueError("Give at least one keyword (or a category id to measure a whole category).")
        data = rt.client().interest_over_time(rt.items(kws, tf, code), category, prop)
        labels = data["labels"] if kws != [""] else [rt.category_label(category)]
        lines = ["# Interest over time", f"{rt.scope(tf, code, category, prop)} · {resolution_words(data['resolution'])}", ""]
        if not data["points"] or not any(max(p["values"], default=0) > 0 for p in data["points"]):
            return "\n".join(lines) + "\n" + "Google Trends has too little search volume to show anything for this. Try a broader term, a longer range or a larger location."
        lines.append(md_table(SUMMARY_HEADERS, summary_rows(data, labels)))
        if points > 0:
            lines += ["", f"## Data ({len(data['points'])} points)", series_table(data, labels, points, rt.tz())]
        lines += ["", SCALE_NOTE + f" [Open in Google Trends]({explore_url(kws, code, tf.value, category, prop)})"]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def interest_by_region(
        keywords: str,
        timeframe: str = "12m",
        geo: str = "",
        resolution: str = "auto",
        include_low_volume: bool = False,
        category: int = 0,
        property: str = "web",
        limit: int = 25,
    ) -> str:
        """Where a term is most popular: countries, regions, cities or metro areas ranked by interest.

        With one term, each place gets 0-100 relative to the strongest place — interest as a share
        of that place's own searches, so a small region can outrank a large one. With 2-5 terms,
        each place shows how its interest splits between them (percentages), i.e. who wins where.

        geo: blank = worldwide (ranks countries); a country ranks its regions; a region its cities.
        resolution: auto, country, region, city, or dma (US metro areas).
        include_low_volume: also list places with little search volume.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        kws = rt.keywords(keywords, cap=MAX_COMPARE)
        data = rt.client().interest_by_region(rt.items(kws, tf, code), category, prop, None if resolution.strip().lower() == "auto" else resolution, include_low_volume)
        level = {"COUNTRY": "country", "REGION": "region", "CITY": "city", "DMA": "metro area"}.get(data["resolution"], "place")
        lines = [f"# Interest by {level}", rt.scope(tf, code, category, prop), ""]
        rows = data["rows"]
        if not rows:
            return "\n".join(lines) + "\n" + "No place has enough search volume for this. Try include_low_volume=true, a longer range or a broader term."
        labels = data["labels"]
        if len(kws) == 1:
            rows = sorted(rows, key=lambda r: -r["values"][0])
            table = md_table(["#", level, "code", "interest"], [[i, r["name"], r["code"], fmt_index(r["values"][0])] for i, r in enumerate(rows, 1)], max_rows=max(1, limit))
            note = f"_100 = the {level} where the term takes the largest share of all searches; it says nothing about the number of searches._"
        else:
            wins = Counter(max(range(len(r["values"])), key=r["values"].__getitem__) for r in rows)
            rows = sorted(rows, key=lambda r: (-max(r["values"]), r["name"]))
            table = md_table(
                [level, "code"] + [safe(lab) for lab in labels] + ["leader"],
                [[r["name"], r["code"]] + [f"{v:.0f}%" for v in r["values"]] + [labels[max(range(len(r["values"])), key=r["values"].__getitem__)]] for r in rows],
                max_rows=max(1, limit),
            )
            lead = ", ".join(f"{safe(labels[i])} leads in {n}" for i, n in wins.most_common())
            note = f"{lead} of {len(rows)}. _Each row is that {level}'s interest split between the terms._"
        return "\n".join(lines) + "\n" + table + "\n\n" + note + f" [Open in Google Trends]({explore_url(kws, code, tf.value, category, prop)})"

    def _related_tables(data: dict, limit: int, what: str) -> list[str]:
        lines = []
        for name, title in (("rising", "Rising"), ("top", "Top")):
            entries = data[name]
            if not entries:
                lines += [f"## {title}", f"No {name} {what}.", ""]
                continue
            if name == "rising":
                rows = [[e["text"], e["growth"]] + ([e["type"], e["mid"]] if what == "topics" else []) for e in entries]
                headers = [what[:-1] if what == "topics" else "query", "growth"]
            else:
                rows = [[e["text"], fmt_index(e["value"])] + ([e["type"], e["mid"]] if what == "topics" else []) for e in entries]
                headers = [what[:-1] if what == "topics" else "query", "relative interest"]
            if what == "topics":
                headers += ["what it is", "id"]
            lines += [f"## {title}", md_table(headers, rows, max_rows=max(1, limit)), ""]
        return lines

    @mcp.tool(annotations=READ)
    @guarded
    def related_queries(keyword: str, timeframe: str = "12m", geo: str = "", category: int = 0, property: str = "web", limit: int = 25) -> str:
        """What else people who search a term also search: the top related queries and the rising ones.

        Rising = the largest growth against the previous period; "Breakout" means more than +5000%,
        usually a query that barely existed before. Top = the most searched, 100 being the most common.
        keyword: one term or topic id. timeframe, geo, category, property: as in interest_over_time.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]
        data = rt.client().related({"keyword": kw, "geo": code, "time": tf.value}, "queries", category, prop)
        lines = [f"# Related queries — {safe(kw)}", rt.scope(tf, code, category, prop), ""]
        if not data["top"] and not data["rising"]:
            return "\n".join(lines) + "\n" + "Google Trends has no related queries for this: the term has too little search volume here. Try a broader term, a longer range or a larger location."
        lines += _related_tables(data, limit, "queries")
        lines.append(f"[Open in Google Trends]({explore_url([kw], code, tf.value, category, prop)})")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def related_topics(keyword: str, timeframe: str = "12m", geo: str = "", category: int = 0, property: str = "web", limit: int = 25) -> str:
        """The topics (people, brands, things) searched alongside a term, top and rising, with their topic ids.

        Google withholds this list from anonymous sessions: without a signed-in cookie
        (GTRENDS_COOKIE or GTRENDS_COOKIES_FILE) it usually comes back empty, and this tool says so
        rather than pretending there are none. related_queries works either way.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]
        client = rt.client()
        data = client.related({"keyword": kw, "geo": code, "time": tf.value}, "topics", category, prop)
        lines = [f"# Related topics — {safe(kw)}", rt.scope(tf, code, category, prop), ""]
        if not data["top"] and not data["rising"]:
            # The session type is stored with the answer: after a restart the cached answer is still explained correctly.
            seen_as = data.get("user_type") or client.user_type
            signed_in = bool(rt.settings.cookie or rt.settings.cookies_file)
            if (seen_as and "LEGIT" not in seen_as) or (seen_as is None and not signed_in):
                return "\n".join(lines) + "\n" + (
                    "Google returned an empty list. It does this for anonymous sessions regardless of the term, so this is "
                    "not evidence that no related topics exist. To get them, give the server a signed-in Google cookie "
                    "(GTRENDS_COOKIE or GTRENDS_COOKIES_FILE — see the docs), or use related_queries, which is not restricted."
                )
            return "\n".join(lines) + "\n" + "Google Trends has no related topics for this term in this range and location."
        lines += _related_tables(data, limit, "topics")
        lines.append("Pass a topic id as the keyword of any tool to measure the topic itself.")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def compare_periods(keyword: str, period: str = "12m", against: str = "year_before", geo: str = "", category: int = 0, property: str = "web", points: int = 13) -> str:
        """One term in two time ranges on the same scale: this year against last year, this quarter against the one before.

        period: the range to look at (3m, 12m, 2026, 2026-03, or explicit dates; not hours).
        against: "year_before" (same dates a year earlier), "previous" (the range just before), or another range.
        The two curves are aligned by position, so day 10 of one sits beside day 10 of the other.
        """
        tf = rt.timeframe(period)
        if tf.start is None or tf.end is None:
            raise ValueError("compare_periods needs a range measured in days or longer (not 1h/4h/1d/7d).")
        mode = against.strip().lower().replace(" ", "_")
        if mode in ("year_before", "last_year", "yoy", ""):
            other = year_before(tf)
        elif mode in ("previous", "previous_period", "prior"):
            other = previous_period(tf)
        else:
            other = parse_timeframe(against)
            if other.start is None:
                raise ValueError("`against` must be year_before, previous, or a range of dates.")
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]

        def explicit(t):
            return f"{t.start.isoformat()} {t.end.isoformat()}"

        items = [{"keyword": kw, "geo": code, "time": explicit(tf)}, {"keyword": kw, "geo": code, "time": explicit(other)}]
        data = rt.client().interest_over_time(items, category, prop)
        labels = [explicit(tf).replace(" ", " → "), explicit(other).replace(" ", " → ")]
        lines = [f"# {safe(kw)} — two periods compared", rt.scope(None, code, category, prop), ""]
        pts = data["points"]
        if not pts or not any(max(p["values"], default=0) > 0 for p in pts):
            return "\n".join(lines) + "\n" + "Google Trends has too little search volume to compare these periods."
        a, b = an.summarize(pts, 0), an.summarize(pts, 1)
        change = an.pct_change(a["average"], b["average"])
        rows = [
            [labels[0], fmt_index(a["average"]), fmt_index(a["peak"]), an.sparkline(an.column(pts, 0))],
            [labels[1], fmt_index(b["average"]), fmt_index(b["peak"]), an.sparkline(an.column(pts, 1))],
        ]
        lines.append(md_table(["period", "average", "peak", "shape"], rows))
        verdict = "no base to compare against (the earlier period shows no interest)" if change is None else f"{fmt_change(change)} in average interest against the comparison period"
        lines += ["", f"**Change:** {verdict}."]
        if points > 0:
            dpts, size = an.downsample(pts, points)
            trows = [[i * size + 1, fmt_index(p["values"][0]), fmt_index(p["values"][1])] for i, p in enumerate(dpts)]
            unit = {"DAY": "day", "WEEK": "week", "MONTH": "month", "HOUR": "hour"}.get(data["resolution"], "point")
            lines += ["", "## Side by side", md_table([f"from {unit}", labels[0], labels[1]], trows)]
            if size > 1:
                lines.append(f"\n_Each row is the mean of {size} consecutive {unit}s, counted from the start of each period._")
        lines += ["", "_Both periods are on one scale: 100 is the highest point across the two._"]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def compare_locations(keyword: str, geos: str, timeframe: str = "12m", category: int = 0, property: str = "web", points: int = 13) -> str:
        """One term in up to 5 countries or regions side by side: where it matters more, and whether the curves move together.

        geos: comma-separated location codes (US, GB, DE) — countries or regions, not worldwide.
        The values say how large a share of each place's own searches the term takes, so a small
        country can score above a large one; they are not search counts.
        """
        tf = rt.timeframe(timeframe)
        prop = rt.prop(property)
        codes = rt.geos(geos, [], cap=MAX_COMPARE)
        if len(codes) < 2:
            raise ValueError("Give at least two location codes, separated by commas.")
        kw = rt.keywords(keyword, cap=1)[0]
        data = rt.client().interest_over_time([{"keyword": kw, "geo": c, "time": tf.value} for c in codes], category, prop)
        labels = [rt.geo_label(c) for c in codes]
        lines = [f"# {safe(kw)} — by location", f"{tf.label} · {PROPERTY_LABELS.get(prop, prop)} · {resolution_words(data['resolution'])}", ""]
        if not data["points"] or not any(max(p["values"], default=0) > 0 for p in data["points"]):
            return "\n".join(lines) + "\n" + "Google Trends has too little search volume in these places to compare them."
        rows = summary_rows(data, labels)
        lines.append(md_table(["location"] + SUMMARY_HEADERS[1:], rows))
        if points > 0:
            lines += ["", f"## Data ({len(data['points'])} points)", series_table(data, codes, points, rt.tz())]
        lines += ["", "_100 = the highest share of local searches reached in any of these places. A higher line means the term matters more there, not that more people searched._"]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def keyword_overview(keyword: str, timeframe: str = "12m", geo: str = "", category: int = 0, property: str = "web") -> str:
        """The whole Google Trends page for one term in a single call: curve, direction, top places, related searches.

        Use it as the first look at a keyword. It makes four requests; a part that Google refuses
        is reported and the rest is still returned.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]
        client = rt.client()
        items = rt.items([kw], tf, code)
        data = client.interest_over_time(items, category, prop)
        label = data["labels"][0] if data["labels"] else kw
        lines = [f"# {safe(label)} — overview", f"{rt.scope(tf, code, category, prop)} · {resolution_words(data['resolution'])}", ""]
        if not data["points"] or max(an.column(data["points"], 0), default=0) <= 0:
            return "\n".join(lines) + "\n" + "Google Trends has too little search volume to show anything for this. Try a broader term, a longer range or a larger location."
        s, m = an.summarize(data["points"], 0), an.momentum(data["points"], 0)
        if m["yoy"] is not None:
            direction = f"{m['label']} ({fmt_change(m['yoy'])} year over year)"
        elif m["change"] is not None:
            # Without a second year there is no telling a season from a trend: give the number, not a verdict.
            direction = f"{fmt_change(m['change'])} in the latest quarter of the range against the one before — over a range this short that may be the season; trend_momentum judges it over five years"
        else:
            direction = m["label"]
        lines.append(
            kv(
                [
                    ("shape", f"`{an.sparkline(an.column(data['points'], 0, complete_only=True), 40)}`"),
                    ("direction", direction),
                    ("average / peak / latest", f"{fmt_index(s['average'])} / {fmt_index(s['peak'])} ({safe(s['peak_label'])}) / {fmt_index(s['latest'])}"),
                ]
            )
        )
        try:
            regions = client.interest_by_region(items, category, prop)
            top = sorted(regions["rows"], key=lambda r: -r["values"][0])[:8]
            if top:
                lines += ["", "## Top places", ", ".join(f"{safe(r['name'])} {fmt_index(r['values'][0])}" for r in top)]
        except TrendsError as e:
            lines += ["", f"_Places could not be loaded: {str(e).split('. ')[0]}._"]
        try:
            rel = client.related(items[0], "queries", category, prop)
            if rel["rising"]:
                lines += ["", "## Rising queries", ", ".join(f"{safe(e['text'])} ({e['growth']})" for e in rel["rising"][:10])]
            if rel["top"]:
                lines += ["", "## Top queries", ", ".join(f"{safe(e['text'])} {fmt_index(e['value'])}" for e in rel["top"][:10])]
        except TrendsError as e:
            lines += ["", f"_Related queries could not be loaded: {str(e).split('. ')[0]}._"]
        lines += ["", f"[Open in Google Trends]({explore_url([kw], code, tf.value, category, prop)})"]
        return "\n".join(lines)
