"""History tools: saved Trending Now snapshots, the watchlist, and SQL over both."""

from __future__ import annotations

import time

from . import analysis as an
from .client import Interrupted, TrendsClient, TrendsError
from .format import clip, fmt_change, fmt_duration, fmt_index, fmt_time, fmt_volume, md_table, safe, short_path
from .matching import TermMatcher, fold
from .runtime import Runtime, hints
from .store import Store
from .timeframes import parse_timeframe
from .tools_analysis import LABEL_WORDS

READ = hints()
LOCAL_READ = hints(open_world=False)
LOCAL_WRITE = hints(read_only=False, open_world=False)  # changes only files on this machine
FETCH_AND_SAVE = hints(read_only=False)


def snapshot(client: TrendsClient, store: Store, geo: str, hours: int = 24) -> dict:
    """Pull Trending Now for one location and save it. Shared by the tool and the CLI."""
    hours = max(1, min(int(hours), 191))
    trends = client.trending_now(geo, hours, fresh=True)  # fresh: a snapshot of a cached answer would record nothing new
    for t in trends:
        t["qkey"] = fold(t["title"])
    return store.save_trending(geo, hours, trends)


def register(mcp, rt: Runtime) -> None:  # noqa: C901
    guarded = rt.guard

    @mcp.tool(annotations=FETCH_AND_SAVE)
    @guarded
    def snapshot_trending(geos: str = "", hours: int = 24) -> str:
        """Save the current Trending Now list to the local history, so it can still be searched after Google drops it.

        Google shows a trend for about a week; after that there is no way to ask what was trending.
        Each call stores every trend for the given countries (a trend seen again is updated, not
        duplicated). Run it on a schedule — `gtrends-mcp-full snapshot US,GB` from cron does the
        same — and use trending_history to query the result.

        geos: comma-separated country or region codes (up to 12); blank = the default location, else US.
        hours: look-back window to save, 1-191. 24 is right for a daily schedule.
        """
        codes = rt.geos(geos, [rt.country("")], cap=12)
        hours = rt.hours(hours)
        client, store = rt.client(), rt.store()
        rows, failed = [], []
        for code in codes:
            try:
                r = snapshot(client, store, code, hours)
                rows.append([code, r["trends"], r["new"], r["updated"]])
            except Interrupted as e:
                failed.append(f"{code} and the rest ({str(e).split('. ')[0]})")
                break
            except TrendsError as e:
                failed.append(f"{code} ({str(e).split('. ')[0]})")
        if not rows:
            raise TrendsError("Nothing could be saved: " + "; ".join(failed))
        out = "# Snapshot saved\n\n" + md_table(["location", "trends", "new", "updated"], rows)
        if failed:
            out += "\n\nNot saved: " + "; ".join(failed) + "."
        return out + f"\n\nStored in {short_path(rt.settings.db_path)}."

    @mcp.tool(annotations=LOCAL_READ)
    @guarded
    def trending_history(contains: str = "", geo: str = "", days: int = 30, min_volume: int = 0, limit: int = 50) -> str:
        """Search the saved Trending Now history: when something trended, how big it got and how long it lasted.

        Works on what snapshot_trending has stored — no network. With no filter it lists the
        biggest saved trends of the period.

        contains: text the trend's title or queries must contain, as whole words.
        geo: one location code; blank = every saved location.
        days: how far back to look, counted from the trend's start.
        """
        store = rt.store()
        coverage = store.snapshot_coverage()
        if not coverage:
            return "No Trending Now history is saved yet. Call snapshot_trending (or schedule `gtrends-mcp-full snapshot <geos>`); history builds from the first snapshot on."
        code = rt.geo(geo) if geo.strip() else None
        days = max(1, min(int(days), 3650))
        since = int(time.time()) - days * 86400
        key = TermMatcher([contains]) if contains.strip() else None
        rows = store.trending_search(code, since, None, min_volume)  # every saved trend of the period: the text filter comes after
        if key:
            rows = [r for r in rows if any(key.matches(q) for q in [r["title"], *r["breakdown"]])]
        tz = rt.tz()
        cov = ", ".join(f"{c['geo']} since {fmt_time(c['first'], tz)[:10]} ({c['snapshots']} snapshot{'s' if c['snapshots'] != 1 else ''})" for c in coverage)
        head = f"# Trending history — past {days} days" + (f", {code}" if code else "") + (f", containing “{safe(clip(contains.strip()))}”" if key else "") + f"\n\nSaved: {cov}."
        if not rows:
            return head + "\n\nNo saved trend matches. History only covers what was snapshotted; widen `days` or check the saved locations above."
        if not key:
            rows.sort(key=lambda r: (-r["volume"], -r["started"]))
        table = md_table(
            ["trend", "location", "searches (peak)", "growth", "started", "lasted", "category"],
            [
                [
                    r["title"],
                    r["geo"],
                    fmt_volume(r["volume"]),
                    f"+{r['growth']:,}%",
                    fmt_time(r["started"], tz),
                    fmt_duration(r["ended"] - r["started"]) if r["ended"] else "still active when last seen",
                    ", ".join(r["categories"]),
                ]
                for r in rows
            ],
            max_rows=max(1, limit),
        )
        return head + f"\n\n{len(rows):,} trend{'s' if len(rows) != 1 else ''}" + (", newest first" if key else ", biggest first") + ".\n\n" + table

    @mcp.tool(annotations=LOCAL_WRITE)
    @guarded
    def watchlist_add(keywords: str, geo: str = "", note: str = "") -> str:
        """Put terms on the watchlist so watchlist_report tracks their direction over time.

        keywords: comma-separated terms or topic ids. geo: the location to track them in (blank = default).
        note: an optional label, e.g. the client or campaign they belong to.
        """
        code = rt.geo(geo)
        kws = rt.keywords(keywords, cap=50)
        store = rt.store()
        note = " ".join(note.split())[:200]  # a label, not a document
        added = [k for k in kws if store.watch_add(k, code, note)]
        total = len(store.watch_list())
        already = len(kws) - len(added)
        return f"Added {len(added)} to the watchlist for {rt.geo_label(code)}" + (f" ({already} already there)" if already else "") + f". {total} watched in all. watchlist_report measures them."

    @mcp.tool(annotations=hints(read_only=False, destructive=True, open_world=False))
    @guarded
    def watchlist_remove(keywords: str, geo: str = "") -> str:
        """Take terms off the watchlist. Blank geo removes them from every location; their past readings are kept."""
        kws = rt.keywords(keywords, cap=50)
        code = rt.geo(geo) if geo.strip() else None
        n = sum(rt.store().watch_remove(k, code) for k in kws)
        return f"Removed {n} watchlist entr{'ies' if n != 1 else 'y'}. {len(rt.store().watch_list())} left."

    @mcp.tool(annotations=FETCH_AND_SAVE)
    @guarded
    def watchlist_report(geo: str = "", limit: int = 10, offset: int = 0) -> str:
        """The watchlist measured now: each term's direction, what changed since the last report, and whether it is trending today.

        Every run stores a reading per term, so the next report can say "was stable, now rising".
        Terms are measured over five years, one at a time (two requests each): `limit` terms per
        call, `offset` to continue. Also checks today's Trending Now list for each term.
        """
        store, client = rt.store(), rt.client()
        watched = store.watch_list()
        if geo.strip():
            code = rt.geo(geo)
            watched = [w for w in watched if w["geo"] == code]
        if not watched:
            return "The watchlist is empty" + (" for that location" if geo.strip() else "") + ". Add terms with watchlist_add."
        page = watched[max(0, offset) : max(0, offset) + max(1, min(limit, 15))]
        if not page:
            return f"No watchlist entries at offset {offset}; there are {len(watched)}."
        tf = parse_timeframe("5y")
        now = int(time.time())
        trending: dict[str, list[dict]] = {}
        rows, skipped = [], []
        for w in page:
            try:
                data = client.interest_over_time([{"keyword": w["keyword"], "geo": w["geo"], "time": tf.value}])
            except Interrupted:
                skipped = [x["keyword"] for x in page[page.index(w) :]]
                break
            except TrendsError as e:
                rows.append([w["keyword"], w["geo"] or "world", f"failed: {str(e).split('. ')[0]}", "", "", "", "", ""])
                continue
            s, m = an.summarize(data["points"], 0), an.momentum(data["points"], 0)
            prev = store.reading_previous(w["keyword"], w["geo"], now)
            store.reading_add(w["keyword"], w["geo"], s["latest"], s["average"], m["yoy"], m["label"], now)
            changed = ""
            if prev and prev["label"] != m["label"]:
                changed = f"was {LABEL_WORDS.get(prev['label'], prev['label'])} on {fmt_time(prev['taken_at'], rt.tz())[:10]}"
            elif prev:
                changed = "no change"
            hot = ""
            tgeo = (w["geo"] or rt.country("")).split("-")[0]
            if tgeo not in trending:
                try:
                    trending[tgeo] = client.trending_now(tgeo, 24)
                except TrendsError:
                    trending[tgeo] = []
            matcher = TermMatcher([w["keyword"]])
            if matcher and not w["keyword"].startswith("/"):
                hits = [t for t in trending[tgeo] if any(matcher.matches(q) for q in [t["title"], *t["breakdown"]])]
                if hits:
                    best = max(hits, key=lambda t: t["volume"])
                    hot = f"TRENDING in {tgeo}: {best['title']} ({fmt_volume(best['volume'])})"
            rows.append(
                [
                    w["keyword"],
                    w["geo"] or "world",
                    LABEL_WORDS[m["label"]],
                    fmt_change(m["yoy"]),
                    fmt_change(m["change"]),
                    fmt_index(s["latest"]) if s["has_data"] else "—",
                    changed,
                    hot or w["note"],
                ]
            )
        if not rows:
            raise TrendsError("No watched term could be measured: Google is rate-limiting or the time for this call ran out. Try again in a few minutes.")
        out = f"# Watchlist report — {len(rows)} of {len(watched)} terms\n\n" + md_table(
            ["term", "location", "direction", "year over year", "last quarter vs before", "latest (0-100)", "since last report", "today / note"], rows
        )
        out += "\n\n_Each term is on its own scale. “Latest” is against that term's own five-year peak._"
        nxt = max(0, offset) + len(page)
        if skipped:
            resume = max(0, offset) + len(page) - len(skipped)
            out += "\n\n_Not measured — Google started rate-limiting or the time for this call ran out: " + ", ".join(safe(k) for k in skipped) + f". Call again with offset={resume}._"
        elif nxt < len(watched):
            out += f"\n\n{len(watched) - nxt} more — call again with offset={nxt}."
        return out

    @mcp.tool(annotations=LOCAL_READ)
    @guarded
    def history_sql(query: str, limit: int = 100) -> str:
        """Run a read-only SQL SELECT on the local data file, for questions the other history tools do not cover.

        Tables:
        trending(geo, title, qkey, started, ended, volume, growth, categories, breakdown, first_seen, last_seen)
          — one row per saved trend; times are unix seconds UTC; categories and breakdown are JSON lists.
        snapshots(id, taken_at, geo, hours, trends) — one row per snapshot_trending run.
        watchlist(keyword, geo, note, added_at)
        readings(keyword, geo, taken_at, latest, average, yoy, label) — one row per watchlist_report measurement.
        Example: SELECT title, volume, datetime(started,'unixepoch') FROM trending WHERE geo='US' ORDER BY volume DESC LIMIT 20
        """
        q = query.strip().rstrip(";")
        if not q.lower().startswith(("select", "with")):
            raise ValueError("Only SELECT statements are allowed.")
        cap = max(1, min(int(limit), 1000))
        cols, rows = rt.store().sql(q, limit=cap + 1)  # one extra row tells a full answer from a cut one
        if not rows:
            return "The query returned no rows."
        more = len(rows) > cap
        rows = rows[:cap]
        return md_table(cols, rows) + f"\n\n{len(rows):,} row{'s' if len(rows) != 1 else ''}" + (f" — cut at the limit of {cap}; there are more" if more else "") + "."
