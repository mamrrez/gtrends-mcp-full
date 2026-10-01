"""Analysis tools: answers worked out from Google Trends data, not just the charts."""

from __future__ import annotations

from datetime import date, datetime, timezone

from . import analysis as an
from .client import MAX_COMPARE, Interrupted, TrendsError
from .format import fmt_change, fmt_index, fmt_points, md_table, safe
from .matching import fold
from .runtime import Runtime, hints
from .timeframes import daily_windows, parse_timeframe
from .tools_core import resolution_words

READ = hints()

LABEL_WORDS = {
    "breakout": "breakout",
    "rising": "rising",
    "declining": "declining",
    "stable": "stable",
    "new": "new",
    "too little data": "too little data",
    "no data": "no data",
}


def momentum_row(label: str, data: dict, index: int = 0) -> tuple[list, dict]:
    s, m = an.summarize(data["points"], index), an.momentum(data["points"], index)
    if not s["has_data"]:
        return [label, "no data", "—", "—", "—", "—", ""], m
    return [
        label,
        LABEL_WORDS[m["label"]],
        fmt_change(m["yoy"]),
        fmt_change(m["change"]),
        fmt_change(m["slope"]) + "/yr" if m["slope"] is not None else "—",
        f"{m['vs_peak']:.0f}%" if m["vs_peak"] is not None else "—",
        an.sparkline(an.column(data["points"], index, complete_only=True)),
    ], m


MOMENTUM_HEADERS = ["term", "direction", "year over year", "last quarter vs the one before", "long-run slope", "now vs its peak", "shape"]


def register(mcp, rt: Runtime) -> None:  # noqa: C901
    guarded = rt.guard

    def one_series(keyword: str, tf, code: str, category: int, prop: str) -> dict:
        return rt.client().interest_over_time(rt.items([keyword], tf, code), category, prop)

    @mcp.tool(annotations=READ)
    @guarded
    def compare_many(keywords: str, timeframe: str = "12m", geo: str = "", anchor: str = "", category: int = 0, property: str = "web") -> str:
        """Rank 6 to 25 terms on one scale — past Google Trends' limit of five per comparison.

        Google rescales every comparison to its own maximum, so two separate five-term charts
        cannot be read against each other. This tool runs the terms in groups that all contain one
        shared *anchor* term and uses it to bring every group onto a single scale.

        anchor: the shared term. Blank = chosen automatically (a mid-sized term among the first five,
        which keeps both large and small terms measurable).
        Returns each term's average, peak and latest value, where the largest average is 100.
        Costs two requests per group of four terms.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        kws = rt.keywords(keywords, cap=25, minimum=2)
        client = rt.client()

        def batch(terms: list[str]) -> dict:
            data = client.interest_over_time(rt.items(terms, tf, code), category, prop)
            sums = [an.summarize(data["points"], i) for i in range(len(terms))]
            return {"labels": terms, "averages": [s["average"] for s in sums], "peaks": [s["peak"] for s in sums], "latest": [s["latest"] for s in sums]}

        if len(kws) <= MAX_COMPARE and not anchor.strip():
            b = batch(kws)
            # One request is already one scale; anchoring on its largest term only sets 100.
            anchor_term = max(zip(b["averages"], kws), key=lambda x: x[0])[1]
            result = an.rescale_batches([b], anchor_term)
            groups, low = 1, []
        else:
            anchor_term = rt.keywords(anchor, cap=1)[0] if anchor.strip() else ""
            batches: list[dict] = []
            rest = list(kws)
            if not anchor_term:
                first = batch(rest[:MAX_COMPARE])
                rest = rest[MAX_COMPARE:]
                ranked = sorted((a, lab) for a, lab in zip(first["averages"], first["labels"]) if a > 0)
                if not ranked:
                    raise TrendsError("None of the first five terms has measurable interest, so no anchor can be chosen. Name one with `anchor`.")
                anchor_term = ranked[len(ranked) // 2][1]
                batches.append(first)
            else:
                rest = [k for k in rest if k != anchor_term]
            failed: list[str] = []
            for i in range(0, len(rest), MAX_COMPARE - 1):
                group = rest[i : i + MAX_COMPARE - 1]
                try:
                    batches.append(batch([anchor_term] + group))
                except Interrupted:
                    failed += group
                    failed += rest[i + MAX_COMPARE - 1 :]
                    break
            if not batches:
                raise TrendsError("No group could be loaded: Google is rate-limiting or the time for this call ran out. Try again in a few minutes.")
            result = an.rescale_batches(batches, anchor_term)
            groups = len(batches)
            # A group where the anchor is squeezed near zero gives imprecise factors: Google reports whole numbers only.
            low = [lab for b in batches[1:] for lab in b["labels"] if lab != anchor_term and 0 < an._value_of(b, anchor_term, "averages") < 3]
            if 0 < an._value_of(batches[0], anchor_term, "averages") < 3:
                # The reference itself is imprecise, so every group placed through it inherits the margin.
                low = [lab for b in batches[1:] for lab in b["labels"] if lab != anchor_term]
            result["unplaced"] += failed

        lines = ["# Many terms on one scale", f"{rt.scope(tf, code, category, prop)} · anchor “{safe(anchor_term)}” · {groups} group{'s' if groups != 1 else ''}", ""]
        if not result["rows"]:
            return "\n".join(lines) + "\nGoogle Trends has too little search volume for any of these terms here to rank them."
        rows = [[i, r["label"], f"{r['average']:.1f}", f"{r['peak']:.0f}", f"{r['latest']:.0f}", "≈" if r["label"] in low else ""] for i, r in enumerate(result["rows"], 1)]
        lines.append(md_table(["#", "term", "average", "peak", "latest", ""], rows))
        lines += ["", "_100 = the average of the most searched term. Peaks can exceed 100 because they are measured against that average._"]
        if low:
            lines.append("_≈ marks terms measured in a group where the anchor was almost invisible; their level is approximate. Re-run with an anchor closer to their size._")
        if result["unplaced"]:
            lines.append("_Could not be placed on the scale: " + ", ".join(safe(u) for u in dict.fromkeys(result["unplaced"])) + " (the anchor showed no interest beside them, or Google stopped answering)._")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def seasonality(keyword: str, geo: str = "", timeframe: str = "5y", category: int = 0, property: str = "web", lead_weeks: int = 8) -> str:
        """The yearly rhythm of a term: which months it peaks and dips, how reliably, and when to publish for it.

        Uses five years of data by default ("all" goes back to 2004). Growth or decline is removed
        first, so a rising term is not mistaken for a seasonal one. Returns a month-by-month index
        (100 = an average month), the peak and trough, how consistent the pattern is from year to
        year, year averages, the months ahead on the current rhythm, and a publishing date.

        lead_weeks: how long before demand starts to climb the content should be live.
        """
        tf = rt.timeframe(timeframe, "5y")
        if tf.days < 730:
            raise ValueError("Seasonality needs at least two years of data; use timeframe 5y (default) or all.")
        lead_weeks = rt.lead_weeks(lead_weeks)
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]
        data = one_series(kw, tf, code, category, prop)
        monthly = an.monthly_means(data["points"], 0)
        res = an.seasonality(monthly)
        label = data["labels"][0] if data["labels"] else kw
        lines = [f"# Seasonality — {safe(label)}", rt.scope(tf, code, category, prop), ""]
        if not res["ok"]:
            return "\n".join(lines) + "\n" + res["reason"]
        idx = res["index"]
        names = an.MONTHS
        seasonal = res["label"] != "not seasonal"
        if seasonal:
            lines.append(f"**{res['label'].capitalize()}.** Peak in {names[res['peak_month'] - 1]} (index {idx[res['peak_month']]:.0f}), low in {names[res['trough_month'] - 1]} (index {idx[res['trough_month']]:.0f}). The peak fell in the same month, give or take one, in {res['consistency'] * 100:.0f}% of {res['years']} full years.")
        else:
            lines.append(f"**Not seasonal.** No month stands out: the strongest is {idx[res['peak_month']] - 100:.0f}% above an average month and the weakest {100 - idx[res['trough_month']]:.0f}% below. Timing matters little for this term.")
        top = max(idx.values())
        rows = [[names[m - 1], f"{idx[m]:.0f}", "█" * max(1, round(idx[m] / top * 20)), ("peak" if m == res["peak_month"] else "low" if m == res["trough_month"] else "") if seasonal else ""] for m in range(1, 13)]
        lines += ["", md_table(["month", "index", "", ""], rows), "", "_Index 100 = an average month for this term; 150 = half as much again._"]

        years = an.yearly_means(monthly)
        yrows, prev = [], None
        for y, avg, n in years:
            yrows.append([str(y) + (" (partial)" if n < 12 else ""), fmt_index(avg), fmt_change(an.pct_change(avg, prev)) if prev is not None and n == 12 else "—"])
            prev = avg if n == 12 else prev
        lines += ["", "## Year by year", md_table(["year", "average", "vs previous full year"], yrows)]

        if seasonal:
            outlook = an.seasonal_outlook(monthly, idx, 6)
            if outlook:
                lines += ["", "## Next six months on this rhythm", ", ".join(f"{names[m - 1]} {fmt_index(v)}" for _, m, v in outlook), "_The pattern projected forward at the recent level — not a forecast of news or growth._"]
            win = an.publish_window(res["peak_month"], idx, lead_weeks)
            today = date.today()
            until_publish, until_peak = an.months_until(win["publish_by"], today), an.months_until(win["peak"], today)
            if until_peak < until_publish:  # the date for this season has passed, the peak has not
                when = f"that date has passed for the coming season, so publish now: the peak is {_months_phrase(until_peak)}"
            else:
                when = _months_phrase(until_publish)
            lines += [
                "",
                "## When to publish",
                f"Interest starts climbing in {names[win['ramp_start'] - 1]} and peaks in {names[win['peak'] - 1]}. "
                f"To be ranking when it does, have the content live by {names[win['publish_by'] - 1]} "
                f"({lead_weeks} weeks ahead) — {when}.",
            ]
        return "\n".join(lines)

    def _months_phrase(n: int) -> str:
        return "this month" if n == 0 else "next month" if n == 1 else f"{n} months from now"

    @mcp.tool(annotations=READ)
    @guarded
    def trend_momentum(keywords: str, geo: str = "", timeframe: str = "5y", category: int = 0, property: str = "web") -> str:
        """Is each term growing, fading, flat or exploding — with the numbers behind the verdict.

        Each term (up to 8) is measured on its own scale, so a small term's shape is not flattened
        by a large one. Per term: year-over-year change of the last quarter, last quarter against
        the quarter before, the long-run slope, where it stands against its own peak, and a label:
        breakout, rising, stable, declining or new.
        """
        tf = rt.timeframe(timeframe, "5y")
        code, prop = rt.geo(geo), rt.prop(property)
        kws = rt.keywords(keywords, cap=8)
        rows, skipped = [], []
        for kw in kws:
            try:
                data = one_series(kw, tf, code, category, prop)
            except Interrupted:
                skipped += kws[kws.index(kw) :]
                break
            rows.append(momentum_row(data["labels"][0] if data["labels"] else kw, data)[0])
        if not rows:
            raise TrendsError("No term could be loaded: Google is rate-limiting or the time for this call ran out. Try again in a few minutes.")
        order = {"breakout": 0, "new": 1, "rising": 2, "stable": 3, "declining": 4, "too little data": 5, "no data": 6}
        rows.sort(key=lambda r: order.get(r[1], 9))
        lines = ["# Momentum", rt.scope(tf, code, category, prop), "", md_table(MOMENTUM_HEADERS, rows)]
        lines += ["", "_Each term is on its own scale: compare directions, not heights (share_of_search and compare_many compare sizes)._"]
        if skipped:
            lines.append("_Not loaded — Google started rate-limiting or the time for this call ran out: " + ", ".join(safe(k) for k in skipped) + "._")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def find_spikes(keyword: str, timeframe: str = "5y", geo: str = "", threshold: float = 2.0, category: int = 0, property: str = "web", limit: int = 15) -> str:
        """The moments a term suddenly jumped: when each spike started, when it peaked, how long it lasted and how big it was.

        A spike is a run of points at least `threshold` times the term's usual level around that
        date (its median over the surrounding year). Use it to date events, launches, outages and
        news cycles, or to tell a one-off burst from real growth.
        """
        tf = rt.timeframe(timeframe, "5y")
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]
        if not 1.2 <= threshold <= 1000:  # written this way round so that NaN is refused too
            raise ValueError("threshold must be a number from 1.2 to 1000 (times the usual level).")
        data = one_series(kw, tf, code, category, prop)
        label = data["labels"][0] if data["labels"] else kw
        lines = [f"# Spikes — {safe(label)}", f"{rt.scope(tf, code, category, prop)} · {resolution_words(data['resolution'])}", ""]
        if len(data["points"]) < 8:
            return "\n".join(lines) + "\n" + f"Only {len(data['points'])} data point{'s' if len(data['points']) != 1 else ''} came back — too few to tell a spike from the usual level. Use a longer range."
        spikes = an.find_spikes(data["points"], 0, threshold)
        if not spikes:
            return "\n".join(lines) + "\n" + f"No stretch reached {threshold:g}× the usual level. The term is steady in this range; lower `threshold` or shorten the range to see smaller bumps."
        spikes.sort(key=lambda s: -s["ratio"])
        unit = {"MINUTE": "minute", "HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month"}.get(data["resolution"], "point")
        rows = [[s["peak_label"], f"{s['ratio']:.1f}×", fmt_index(s["peak_value"]), s["start_label"], f"{s['length']} {unit}{'s' if s['length'] != 1 else ''}", "still running" if s["ongoing"] else ""] for s in spikes[: max(1, limit)]]
        lines.append(md_table(["peaked", "vs usual level", "value", "started", "lasted", ""], rows))
        lines += ["", f"`{an.sparkline(an.column(data['points'], 0), 60)}`", f"{len(spikes)} spike{'s' if len(spikes) != 1 else ''} at {threshold:g}× or more, largest first."]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def share_of_search(brand: str, competitors: str, timeframe: str = "12m", geo: str = "", category: int = 0, property: str = "web") -> str:
        """Share of search: your brand's portion of all searches for you and up to 4 competitors, and how it is moving.

        Share of search tracks market share closely in many categories and moves before it, which
        makes it a cheap leading indicator. Returns each brand's share over the whole range, at
        its start and at its end (first and last fifth of the range), and the change in points.
        Use topic ids (find_topic) when a brand name has other meanings.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        me = rt.keywords(brand, cap=1)[0]
        others = [k for k in rt.keywords(competitors, cap=MAX_COMPARE - 1) if k != me]
        if not others:
            raise ValueError("Give at least one competitor.")
        terms = [me] + others
        data = rt.client().interest_over_time(rt.items(terms, tf, code), category, prop)
        labels = data["labels"] if len(data["labels"]) == len(terms) else terms
        sos = an.share_of_search(data["points"], len(terms))
        lines = ["# Share of search", f"{rt.scope(tf, code, category, prop)} · {resolution_words(data['resolution'])}", ""]
        if sum(sos["overall"]) <= 0:
            return "\n".join(lines) + "\n" + "Google Trends has too little search volume for these brands here to compute a share."
        rows = []
        for i, lab in enumerate(labels):
            delta = sos["end"][i] - sos["start"][i]
            series = [row[i] for row in sos["series"]]
            rows.append([lab + (" (you)" if i == 0 else ""), f"{sos['overall'][i]:.1f}%", f"{sos['start'][i]:.1f}%", f"{sos['end'][i]:.1f}%", fmt_points(delta), an.sparkline(series)])
        order = sorted(range(len(rows)), key=lambda i: -sos["overall"][i])
        lines.append(md_table(["brand", "share overall", "at the start", "at the end", "change", "share over time"], [rows[i] for i in order]))
        d0 = sos["end"][0] - sos["start"][0]
        rank = order.index(0) + 1
        lines += ["", f"**{safe(labels[0])}** is #{rank} of {len(terms)} with {sos['overall'][0]:.1f}% of searches, {'up' if d0 >= 0 else 'down'} {abs(d0):.1f} points across the range.", "", "_Shares are of the searches for these terms only, and are as good as the terms: a brand name that is also an ordinary word inflates its share._"]
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def content_calendar(keywords: str, geo: str = "", lead_weeks: int = 8, category: int = 0, property: str = "web") -> str:
        """A publishing calendar from seasonality: for up to 8 topics, when each peaks and the date its content must be live.

        Each term's five-year rhythm is measured; the result is sorted by what needs publishing
        soonest. Terms without a yearly pattern are listed as evergreen.
        lead_weeks: how long before demand starts to climb the content should be published.
        """
        code, prop = rt.geo(geo), rt.prop(property)
        kws = rt.keywords(keywords, cap=8)
        lead_weeks = rt.lead_weeks(lead_weeks)
        tf = parse_timeframe("5y")
        today = date.today()
        names = an.MONTHS
        seasonal, evergreen, skipped = [], [], []
        for kw in kws:
            try:
                data = one_series(kw, tf, code, category, prop)
            except Interrupted:
                skipped += kws[kws.index(kw) :]
                break
            monthly = an.monthly_means(data["points"], 0)
            res = an.seasonality(monthly)
            if not res["ok"]:
                evergreen.append([kw, "too little data", "—"])
                continue
            m = an.momentum(data["points"], 0)
            if res["label"] in ("not seasonal", "uneven (large swings, but not at the same time each year)"):
                evergreen.append([kw, res["label"].split(" (")[0], LABEL_WORDS[m["label"]]])
                continue
            win = an.publish_window(res["peak_month"], res["index"], lead_weeks)
            wait = an.months_until(win["publish_by"], today)
            if an.months_until(win["peak"], today) < wait:  # this season's date has passed, its peak has not
                wait, when = -1, "overdue — publish now"
            else:
                when = "this month" if wait == 0 else f"in {wait} mo"
            seasonal.append((wait, [kw, names[win["publish_by"] - 1], when, names[win["ramp_start"] - 1], names[win["peak"] - 1], f"{res['index'][res['peak_month']]:.0f}", f"{res['consistency'] * 100:.0f}%", LABEL_WORDS[m["label"]]]))
        if not seasonal and not evergreen:
            raise TrendsError("No term could be loaded: Google is rate-limiting or the time for this call ran out. Try again in a few minutes.")
        lines = ["# Content calendar", f"{rt.scope(None, code, category, prop)} · five-year rhythm · content live {lead_weeks} weeks before demand climbs", ""]
        if seasonal:
            seasonal.sort(key=lambda x: x[0])
            lines += [md_table(["topic", "publish by", "", "demand climbs from", "peaks in", "peak index", "same time each year", "long-run direction"], [r for _, r in seasonal]), "", "_Peak index: 100 = an average month. “Same time each year”: share of years whose peak fell within a month of the usual one._"]
        if evergreen:
            lines += ["", "## No yearly pattern — publish any time", md_table(["topic", "pattern", "long-run direction"], evergreen)]
        if skipped:
            lines.append("\n_Not loaded — Google started rate-limiting or the time for this call ran out: " + ", ".join(safe(k) for k in skipped) + "._")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def daily_history(keyword: str, start: str, end: str = "", geo: str = "", category: int = 0, property: str = "web", points: int = 30) -> str:
        """Day-by-day interest over a long range — Google Trends itself only gives daily data for up to ~9 months.

        The range is fetched as overlapping 8-month windows and the windows are joined on one
        0-100 scale using the days they share. Up to about 4 years (8 windows, two requests each).
        Also returns the weekday pattern: which days of the week the term is searched most.

        start, end: YYYY-MM-DD; end defaults to today.
        points: rows to return (the daily series is averaged down to this many); the summary is always given.
        """
        tf = parse_timeframe(f"{start.strip()} {(end.strip() or date.today().isoformat())}")
        code, prop = rt.geo(geo), rt.prop(property)
        kw = rt.keywords(keyword, cap=1)[0]
        windows = daily_windows(tf.start, tf.end)
        if len(windows) > 8:
            raise ValueError(f"That range needs {len(windows)} windows; the limit is 8 (about 4 years). Shorten it, or use interest_over_time for weekly data.")
        client = rt.client()
        fetched, missing = [], 0
        for a, b in windows:
            try:
                data = client.interest_over_time([{"keyword": kw, "geo": code, "time": f"{a.isoformat()} {b.isoformat()}"}], category, prop)
            except Interrupted:
                missing = len(windows) - len(fetched)
                break
            if data["resolution"] not in ("DAY", ""):
                raise TrendsError(f"Google answered a window with {resolution_words(data['resolution'])} instead of daily data; the daily limit may have changed.")
            fetched.append([(datetime.fromtimestamp(p["t"], timezone.utc).date(), p["values"][0]) for p in data["points"] if not p.get("partial")])
        if not fetched:
            raise TrendsError("No window could be loaded: Google is rate-limiting or the time for this call ran out. Try again in a few minutes.")
        series, warnings = an.stitch(fetched)
        if not series:
            return f"# Daily history — {safe(kw)}\n\nGoogle Trends returned no daily data for this term in {rt.geo_label(code)} over this range: too little search volume."
        lines = [f"# Daily history — {safe(kw)}", f"{series[0][0]} to {series[-1][0]} · {rt.scope(None, code, category, prop)} · {len(series):,} days from {len(fetched)} window{'s' if len(fetched) != 1 else ''}", ""]
        vals = [v for _, v in series]
        if max(vals, default=0) <= 0:
            return "\n".join(lines) + "\n" + "Google Trends has too little search volume for daily data on this term."
        peak_day = max(series, key=lambda x: x[1])
        lines += [f"`{an.sparkline(vals, 60)}`", f"Average {fmt_index(an.mean(vals))}, peak 100 on {peak_day[0]}, latest {fmt_index(vals[-1])} on {series[-1][0]}."]
        wd = an.weekday_profile(series)
        days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        lines += ["", "## Weekday pattern", ", ".join(f"{d} {v:.0f}" for d, v in zip(days, wd)) + "  _(100 = an average day)_"]
        if points > 0:
            pts = [{"t": 0, "label": d.isoformat(), "values": [v], "partial": False} for d, v in series]
            dpts, size = an.downsample(pts, points)
            lines += ["", "## Data", md_table(["from", "interest"], [[p["label"], fmt_index(p["values"][0])] for p in dpts])]
            if size > 1:
                lines.append(f"\n_Each row is the mean of {size} days starting on that date. Raise `points` (up to {len(series)}) for finer rows._")
        for w in warnings:
            lines.append(f"\n_Warning: {w}_")
        if missing:
            lines.append(f"\n_Warning: Google started rate-limiting or the time for this call ran out; the last {missing} window{'s' if missing != 1 else ''} could not be loaded, so the series stops early._")
        return "\n".join(lines)

    @mcp.tool(annotations=READ)
    @guarded
    def keyword_ideas(seeds: str, timeframe: str = "12m", geo: str = "", category: int = 0, property: str = "web", limit: int = 40) -> str:
        """Keyword ideas from what people actually search next: rising and top related queries for up to 5 seed terms, merged.

        The same query coming from several seeds is listed once; each idea shows which seeds it came from —
        an idea related to several seeds sits at the centre of the subject. Rising ideas are sorted
        breakouts first: these are the searches that did not exist a period ago.
        Costs two paced requests per seed.
        """
        tf = rt.timeframe(timeframe)
        code, prop = rt.geo(geo), rt.prop(property)
        kws = rt.keywords(seeds, cap=5)
        client = rt.client()
        seed_keys = {fold(k) for k in kws}
        rising: dict[str, dict] = {}
        top: dict[str, dict] = {}
        skipped: list[str] = []
        for kw in kws:
            try:
                rel = client.related({"keyword": kw, "geo": code, "time": tf.value}, "queries", category, prop)
            except Interrupted:
                skipped += kws[kws.index(kw) :]
                break
            for bucket, entries in ((rising, rel["rising"]), (top, rel["top"])):
                for e in entries:
                    key = fold(e["text"])
                    if not key or key in seed_keys:
                        continue
                    slot = bucket.setdefault(key, {"text": e["text"], "value": 0.0, "breakout": False, "seeds": []})
                    slot["value"] = max(slot["value"], e["value"])
                    slot["breakout"] = slot["breakout"] or e["breakout"]
                    if kw not in slot["seeds"]:
                        slot["seeds"].append(kw)
        lines = ["# Keyword ideas", f"{rt.scope(tf, code, category, prop)} · seeds: {', '.join(safe(k) for k in kws)}", ""]
        if not rising and not top:
            if skipped:
                raise TrendsError("No seed could be loaded: Google is rate-limiting or the time for this call ran out. Try again in a few minutes.")
            return "\n".join(lines) + "\n" + "Google Trends has no related queries for these seeds here: too little search volume. Try broader seeds, a longer range or a larger location."
        if rising:
            ranked = sorted(rising.values(), key=lambda s: (-len(s["seeds"]), not s["breakout"], -s["value"]))
            rows = [[s["text"], "Breakout" if s["breakout"] else f"+{s['value']:,.0f}%", ", ".join(s["seeds"]), "in rising and top" if fold(s["text"]) in top else ""] for s in ranked]
            lines += [f"## Rising ({len(rows)})", md_table(["query", "growth", "from seed", ""], rows, max_rows=max(1, limit)), ""]
        if top:
            ranked = sorted(top.values(), key=lambda s: (-len(s["seeds"]), -s["value"]))
            rows = [[s["text"], fmt_index(s["value"]), ", ".join(s["seeds"])] for s in ranked]
            lines += [f"## Top ({len(rows)})", md_table(["query", "relative interest", "from seed"], rows, max_rows=max(1, limit)), ""]
        lines.append("_Growth and interest are relative to each seed's own related list, not search counts. compare_many puts a shortlist on one scale._")
        if skipped:
            lines.append("_Not loaded — Google started rate-limiting or the time for this call ran out: " + ", ".join(safe(k) for k in skipped) + "._")
        return "\n".join(lines)
