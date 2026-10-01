"""The arithmetic behind the analysis tools. Pure functions, no network.

Google Trends numbers are *relative*: every answer is rescaled so its own
highest point is 100. Two consequences shape everything here:

- values from two separate requests cannot be compared until they are put on
  one scale through something both requests contain (:func:`rescale_batches`,
  :func:`stitch`);
- what a series is good for is its *shape* — when it peaks, whether it grows,
  how one term's share moves against another's — never a search count.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

BARS = "▁▂▃▄▅▆▇█"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# -- small helpers -----------------------------------------------------------


def sparkline(values: Sequence[float], width: int = 24) -> str:
    """A one-line chart. Scaled to the series' own range so a flat low series is still readable."""
    vals = [float(v) for v in values]
    if not vals:
        return ""
    if len(vals) > width:
        vals = bucket_means(vals, width)
    lo, hi = min(vals), max(vals)
    if hi <= 0:
        return BARS[0] * len(vals)
    span = hi - lo
    if span == 0:
        return BARS[3] * len(vals)
    return "".join(BARS[min(7, int((v - lo) / span * 7.999))] for v in vals)


def bucket_means(values: Sequence[float], buckets: int) -> list[float]:
    """Average ``values`` into ``buckets`` consecutive groups of near-equal size."""
    n = len(values)
    if buckets <= 0 or n <= buckets:
        return [float(v) for v in values]
    out = []
    for i in range(buckets):
        a, b = i * n // buckets, (i + 1) * n // buckets
        chunk = values[a:b] or values[a : a + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def downsample(points: list[dict], max_points: int) -> tuple[list[dict], int]:
    """Merge consecutive points so at most ``max_points`` remain. Returns ``(points, group_size)``.

    A merged point keeps the first timestamp and label of its group and the
    mean of each value; it is partial if any of its members is.
    """
    n = len(points)
    if max_points <= 0 or n <= max_points:
        return points, 1
    size = -(-n // max_points)
    out = []
    for i in range(0, n, size):
        group = points[i : i + size]
        width = len(group[0]["values"])
        out.append(
            {
                "t": group[0]["t"],
                "label": group[0]["label"],
                "end_label": group[-1]["label"],
                "values": [sum(p["values"][k] for p in group) / len(group) for k in range(width)],
                "partial": any(p.get("partial") for p in group),
            }
        )
    return out, size


def column(points: list[dict], index: int, complete_only: bool = False) -> list[float]:
    """One series as a list. ``complete_only`` leaves out points still being collected — unless that leaves nothing."""
    if complete_only and any(not p.get("partial") for p in points):
        points = [p for p in points if not p.get("partial")]
    return [p["values"][index] for p in points if index < len(p["values"])]


def point_date(point: dict, tz=timezone.utc) -> date:
    t = point["t"][0] if isinstance(point["t"], list) else point["t"]
    return datetime.fromtimestamp(int(t), tz).date()


def pct_change(cur: float, prev: float) -> float | None:
    """Percent change, or ``None`` when there is no base to compare against."""
    if prev <= 0:
        return None
    return (cur - prev) / prev * 100.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# -- one series ----------------------------------------------------------------


def summarize(points: list[dict], index: int) -> dict:
    """Average, peak, latest complete value and the last partial value of one series."""
    complete = [p for p in points if not p.get("partial")] or points
    vals = [p["values"][index] for p in complete]
    if not vals:
        return {"average": 0.0, "peak": 0.0, "peak_label": "", "low": 0.0, "latest": 0.0, "latest_label": "", "partial": None, "has_data": False}
    peak_i = max(range(len(vals)), key=vals.__getitem__)
    partial = next((p["values"][index] for p in reversed(points) if p.get("partial")), None)
    return {
        "average": mean(vals),
        "peak": vals[peak_i],
        "peak_label": _label(complete[peak_i]),
        "low": min(vals),
        "latest": vals[-1],
        "latest_label": _label(complete[-1]),
        "partial": partial,
        "has_data": max(vals) > 0,
    }


def _label(point: dict) -> str:
    lab = point.get("label", "")
    return lab[0] if isinstance(lab, list) else lab


def momentum(points: list[dict], index: int) -> dict:
    """Where a series is heading.

    Works on any series at least ~26 points long; the year-over-year figures
    need a little over a year of data. Windows are measured in points, sized
    from the series itself: the *recent* window is the last quarter-year.

    Returns ``recent`` and ``previous`` window means, their change, the
    year-over-year change of the recent window, the slope of a straight line
    through the series (percent of the mean per year), the position of the
    latest value against the series' own history, and a label:
    ``breakout``, ``rising``, ``declining``, ``stable``, ``new``, ``too little data`` or ``no data``.
    """
    complete = [p for p in points if not p.get("partial")] or points
    vals = [p["values"][index] for p in complete]
    n = len(vals)
    out: dict = {"points": n, "label": "no data", "recent": 0.0, "previous": 0.0, "change": None, "yoy": None, "slope": None, "vs_peak": None}
    if not vals or max(vals) <= 0:
        return out
    if n < 8:
        out["label"] = "too little data"  # there is interest, but not enough points to say where it is heading
        return out
    per_year = _points_per_year(complete)
    window = max(2, min(n // 3, round(per_year / 4))) if per_year else max(2, n // 4)
    recent = mean(vals[-window:])
    previous = mean(vals[-2 * window : -window])
    out["recent"], out["previous"] = recent, previous
    out["change"] = pct_change(recent, previous)
    year = round(per_year) if per_year else 0
    if year and n >= year + window:
        out["yoy"] = pct_change(recent, mean(vals[-year - window : -year]))
    out["slope"] = _slope_pct_per_year(vals, per_year or n)
    out["vs_peak"] = recent / max(vals) * 100.0

    first_half = mean(vals[: n // 2])
    yoy, change = out["yoy"], out["change"]
    if first_half < 1.0 and recent >= 10:
        out["label"] = "new"  # nothing to speak of in the first half, real interest now
    elif (yoy is not None and yoy >= 150) or (yoy is None and change is not None and change >= 150):
        out["label"] = "breakout"
    elif yoy is not None:
        out["label"] = "rising" if yoy >= 15 else "declining" if yoy <= -15 else "stable"
    elif change is not None:
        out["label"] = "rising" if change >= 20 else "declining" if change <= -20 else "stable"
    else:
        out["label"] = "rising" if recent > 0 else "no data"
    return out


def _points_per_year(points: list[dict]) -> float:
    if len(points) < 2:
        return 0.0
    t0 = points[0]["t"][0] if isinstance(points[0]["t"], list) else points[0]["t"]
    t1 = points[-1]["t"][0] if isinstance(points[-1]["t"], list) else points[-1]["t"]
    span = (int(t1) - int(t0)) / 86400.0
    if span <= 0:
        return 0.0
    return (len(points) - 1) / span * 365.25


def _slope_pct_per_year(vals: Sequence[float], per_year: float) -> float | None:
    n = len(vals)
    avg = mean(vals)
    if n < 3 or avg <= 0:
        return None
    mx = (n - 1) / 2
    num = sum((i - mx) * (v - avg) for i, v in enumerate(vals))
    den = sum((i - mx) ** 2 for i in range(n))
    return (num / den) * per_year / avg * 100.0 if den else None


def monthly_means(points: list[dict], index: int, tz=timezone.utc) -> dict[tuple[int, int], float]:
    """``{(year, month): mean}`` from a daily, weekly or monthly series. Partial points are left out."""
    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    # A weekly point is stamped with the Sunday that starts it; the middle of the week is the fairer month.
    shift = timedelta(days=3) if 300 > _points_per_year(points) > 40 else timedelta(0)
    for p in points:
        if p.get("partial"):
            continue
        d = point_date(p, tz) + shift
        buckets[(d.year, d.month)].append(p["values"][index])
    return {k: mean(v) for k, v in sorted(buckets.items())}


def seasonality(monthly: dict[tuple[int, int], float]) -> dict:
    """The yearly rhythm of a series, from its monthly means.

    Each month is divided by the mean of the twelve months centred on it — so a
    term that is growing or shrinking is not mistaken for a seasonal one — and
    the ratios are averaged by calendar month. An index of 100 is an average
    month; 150 means half as much again.

    ``strength`` is how much of the index's swing there is (peak minus trough);
    ``consistency`` is the share of years whose own strongest month falls
    within one month of the overall peak. Needs at least 24 months.
    """
    keys = sorted(monthly)
    if len(keys) < 24:
        return {"ok": False, "reason": "At least two full years of data are needed to measure seasonality."}
    vals = [monthly[k] for k in keys]
    if max(vals) <= 0:
        return {"ok": False, "reason": "The series has no measurable interest."}
    ratios: dict[int, list[float]] = defaultdict(list)
    for i, (_, month) in enumerate(keys):
        lo, hi = i - 6, i + 6
        if lo < 0 or hi >= len(vals):
            continue
        # centred 12-month mean: half weight on the two ends
        base = (0.5 * vals[lo] + sum(vals[lo + 1 : hi]) + 0.5 * vals[hi]) / 12.0
        if base > 0:
            ratios[month].append(vals[i] / base)
    if len(ratios) < 12:
        return {"ok": False, "reason": "Not every calendar month has data."}
    raw = {m: mean(r) for m, r in ratios.items()}
    norm = mean(list(raw.values())) or 1.0
    index = {m: raw[m] / norm * 100.0 for m in range(1, 13)}
    peak = max(index, key=index.__getitem__)
    trough = min(index, key=index.__getitem__)
    strength = index[peak] - index[trough]

    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    for (y, m), v in monthly.items():
        by_year[y][m] = v
    full_years = {y: ms for y, ms in by_year.items() if len(ms) == 12 and max(ms.values()) > 0}
    hits = sum(1 for ms in full_years.values() if _month_distance(max(ms, key=ms.__getitem__), peak) <= 1)
    consistency = hits / len(full_years) if full_years else 0.0

    if strength >= 60 and consistency >= 0.6:
        label = "strongly seasonal"
    elif strength >= 30 and consistency >= 0.5:
        label = "seasonal"
    elif strength >= 30:
        label = "uneven (large swings, but not at the same time each year)"
    else:
        label = "not seasonal"
    return {
        "ok": True,
        "index": index,
        "peak_month": peak,
        "trough_month": trough,
        "strength": strength,
        "consistency": consistency,
        "years": len(full_years),
        "label": label,
        "high_months": [m for m in range(1, 13) if index[m] >= 115],
        "low_months": [m for m in range(1, 13) if index[m] <= 85],
    }


def _month_distance(a: int, b: int) -> int:
    d = abs(a - b) % 12
    return min(d, 12 - d)


def yearly_means(monthly: dict[tuple[int, int], float]) -> list[tuple[int, float, int]]:
    """``(year, mean, months_with_data)`` per year."""
    by_year: dict[int, list[float]] = defaultdict(list)
    for (y, _), v in monthly.items():
        by_year[y].append(v)
    return [(y, mean(v), len(v)) for y, v in sorted(by_year.items())]


def seasonal_outlook(monthly: dict[tuple[int, int], float], index: dict[int, float], months_ahead: int = 6) -> list[tuple[int, int, float]]:
    """Expected level for the coming months: the recent deseasonalised level times each month's index.

    It is a rhythm projected forward, not a forecast of news or growth.
    Returns ``(year, month, expected)`` on the scale of ``monthly``.
    """
    keys = sorted(monthly)
    if not keys:
        return []
    recent = keys[-6:]
    level = mean([monthly[k] / (index[k[1]] / 100.0) for k in recent if index.get(k[1], 0) > 0])
    y, m = keys[-1]
    out = []
    for _ in range(months_ahead):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        out.append((y, m, level * index[m] / 100.0))
    return out


def publish_window(peak_month: int, index: dict[int, float], lead_weeks: int = 8) -> dict:
    """When interest starts climbing toward the peak and when content should be live.

    The climb starts at the first month, walking back from the peak, whose
    index is still above average; content should be published ``lead_weeks``
    before that so it is indexed and ranking when demand arrives.
    """
    start = peak_month
    for _ in range(11):
        prev = 12 if start == 1 else start - 1
        if index[prev] < 100 or index[prev] > index[start]:
            break
        start = prev
    lead_months = max(1, round(lead_weeks / 4.345))
    publish = (start - lead_months - 1) % 12 + 1
    return {"ramp_start": start, "publish_by": publish, "peak": peak_month}


def months_until(month: int, today: date) -> int:
    return (month - today.month) % 12


def find_spikes(points: list[dict], index: int, threshold: float = 2.0, min_value: float = 10.0) -> list[dict]:
    """Stretches where a series runs far above its usual level.

    The usual level at each point is the median of the surrounding year (or of
    the whole series when it is shorter); a spike is a run of points at least
    ``threshold`` times that level and at least ``min_value`` high. Each spike
    reports its start, its peak, its length and how many times the usual level
    it reached.
    """
    vals = [p["values"][index] for p in points]
    n = len(vals)
    if n < 8:
        return []
    per_year = _points_per_year(points)
    half = max(4, min(n // 2, round((per_year or n) / 2)))
    spikes: list[dict] = []
    current: dict | None = None
    for i, v in enumerate(vals):
        window = vals[max(0, i - half) : i + half + 1]
        base = max(statistics.median(window), 1.0)
        if v >= threshold * base and v >= min_value:
            if current is None:
                current = {"start": i, "end": i, "peak": i, "ratio": v / base}
            else:
                current["end"] = i
                if v > vals[current["peak"]]:
                    current["peak"], current["ratio"] = i, v / base
        elif current is not None:
            spikes.append(current)
            current = None
    if current is not None:
        spikes.append(current)
    return [
        {
            "start_label": _label(points[s["start"]]),
            "peak_label": _label(points[s["peak"]]),
            "peak_value": vals[s["peak"]],
            "length": s["end"] - s["start"] + 1,
            "ratio": s["ratio"],
            "ongoing": s["end"] == n - 1,
        }
        for s in spikes
    ]


# -- several series ------------------------------------------------------------


def share_of_search(points: list[dict], count: int) -> dict:
    """Each term's share of the combined interest, overall and at both ends of the range.

    ``start`` and ``end`` are the shares over the first and last fifth of the
    range, so the change between them is a trend rather than one noisy point.
    """
    complete = [p for p in points if not p.get("partial")] or points
    n = len(complete)
    if not n:
        return {"overall": [0.0] * count, "start": [0.0] * count, "end": [0.0] * count, "series": []}

    def shares(rows: list[dict]) -> list[float]:
        sums = [sum(p["values"][k] for p in rows) for k in range(count)]
        total = sum(sums)
        return [s / total * 100.0 if total else 0.0 for s in sums]

    edge = max(1, n // 5)
    series = []
    for p in complete:
        total = sum(p["values"][:count])
        series.append([v / total * 100.0 if total else 0.0 for v in p["values"][:count]])
    return {"overall": shares(complete), "start": shares(complete[:edge]), "end": shares(complete[-edge:]), "series": series}


def rescale_batches(batches: list[dict], anchor: str) -> dict:
    """Put several five-term comparisons on one scale through a term they all contain.

    Each batch is ``{"labels": [...], "averages": [...], "peaks": [...]}`` and
    includes ``anchor``. The first batch sets the scale; every other batch is
    multiplied by (anchor's average in the first batch ÷ its average in that
    batch). The result is then scaled so the highest average is 100.

    A batch where the anchor averages zero cannot be placed (the other terms
    dwarf it); its terms are returned in ``unplaced``.
    """
    if not batches:
        return {"rows": [], "unplaced": []}
    ref = _value_of(batches[0], anchor, "averages")
    rows: dict[str, dict] = {}
    unplaced: list[str] = []
    for b in batches:
        a = _value_of(b, anchor, "averages")
        if a <= 0 or ref <= 0:
            unplaced += [lab for lab in b["labels"] if lab != anchor]
            continue
        factor = ref / a
        for i, lab in enumerate(b["labels"]):
            if lab == anchor and lab in rows:
                continue
            rows[lab] = {"label": lab, "average": b["averages"][i] * factor, "peak": b["peaks"][i] * factor, "latest": b["latest"][i] * factor}
    top = max((r["average"] for r in rows.values()), default=0.0)
    scale = 100.0 / top if top > 0 else 1.0
    out = [{**r, "average": r["average"] * scale, "peak": r["peak"] * scale, "latest": r["latest"] * scale} for r in rows.values()]
    out.sort(key=lambda r: -r["average"])
    return {"rows": out, "unplaced": unplaced}


def _value_of(batch: dict, label: str, field: str) -> float:
    try:
        return float(batch[field][batch["labels"].index(label)])
    except (ValueError, IndexError):
        return 0.0


def stitch(windows: list[list[tuple[date, float]]]) -> tuple[list[tuple[date, float]], list[str]]:
    """Join overlapping daily windows into one series on a single 0-100 scale.

    Each window was scaled by Google to its own maximum. Consecutive windows
    share some days; the later one is multiplied by (sum over the shared days
    in the series so far ÷ its own sum over those days). Where the windows
    disagree on a shared day, the two values are averaged.
    """
    warnings: list[str] = []
    merged: dict[date, float] = {}
    for w, win in enumerate(windows):
        if not win:
            continue
        if not merged:
            merged = dict(win)
            continue
        shared = [d for d, _ in win if d in merged]
        ours = sum(merged[d] for d in shared)
        theirs = sum(v for d, v in win if d in merged)
        if shared and ours > 0 and theirs > 0:
            factor = ours / theirs
            if theirs < 2 * len(shared):
                # Google reports whole numbers; an overlap that averages under 2 gives a factor with a wide margin.
                warnings.append(f"Window {w + 1} overlaps the one before it at a very low level, so everything from it onward is on an approximate scale.")
        else:
            factor = 1.0
            warnings.append(f"Window {w + 1} shares no measurable interest with the one before it, so its level relative to earlier dates is a guess.")
        for d, v in win:
            scaled = v * factor
            merged[d] = (merged[d] + scaled) / 2.0 if d in merged else scaled
    top = max(merged.values(), default=0.0)
    scale = 100.0 / top if top > 0 else 1.0
    return [(d, merged[d] * scale) for d in sorted(merged)], warnings


def weekly_from_daily(series: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Mean per ISO week (labelled by its Monday); a trailing week of fewer than 4 days is dropped."""
    weeks: dict[date, list[float]] = defaultdict(list)
    for d, v in series:
        weeks[d - timedelta(days=d.weekday())].append(v)
    return [(d, mean(v)) for d, v in sorted(weeks.items()) if len(v) >= 4]


def weekday_profile(series: list[tuple[date, float]]) -> list[float]:
    """Mean interest per weekday (Monday first) as an index where 100 is the average day."""
    days: dict[int, list[float]] = defaultdict(list)
    for d, v in series:
        days[d.weekday()].append(v)
    means = [mean(days.get(i, [])) for i in range(7)]
    avg = mean([m for m in means if m > 0])
    return [m / avg * 100.0 if avg else 0.0 for m in means]
