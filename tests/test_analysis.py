import math
from datetime import date, datetime, timedelta, timezone

from gtrends_mcp_full import analysis as an


def weekly(fn, weeks=261, end=date(2026, 9, 27), partial_last=False):
    """A weekly series: ``fn(date) -> value`` for each week ending at ``end``."""
    pts = []
    for i in range(weeks):
        d = end - timedelta(weeks=weeks - 1 - i)
        t = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
        pts.append({"t": t, "label": d.isoformat(), "values": [float(fn(d))], "partial": False})
    if partial_last:
        pts[-1]["partial"] = True
    return pts


def summer(d):
    return 50 + 45 * math.cos(2 * math.pi * (d.timetuple().tm_yday - 196) / 365.25)


def test_sparkline_scales_to_its_own_range_and_handles_edges():
    assert an.sparkline([]) == ""
    assert an.sparkline([0, 0, 0]) == "▁▁▁"
    assert an.sparkline([5, 5, 5]) == "▄▄▄"
    line = an.sparkline([1, 2, 3, 4, 5, 6, 7, 8])
    assert line[0] == "▁" and line[-1] == "█"
    assert len(an.sparkline(list(range(100)), width=20)) == 20


def test_downsample_averages_and_keeps_partial_flag():
    pts = [{"t": i, "label": f"p{i}", "values": [float(i), 10.0], "partial": i == 9} for i in range(10)]
    out, size = an.downsample(pts, 5)
    assert size == 2 and len(out) == 5
    assert out[0]["values"] == [0.5, 10.0] and out[0]["label"] == "p0" and out[0]["end_label"] == "p1"
    assert out[-1]["partial"] is True
    assert an.downsample(pts, 20) == (pts, 1)


def test_summarize_ignores_the_partial_point():
    pts = weekly(lambda d: 40, weeks=10)
    pts[-1].update(values=[3.0], partial=True)
    pts[4]["values"] = [100.0]
    s = an.summarize(pts, 0)
    assert s["peak"] == 100 and s["peak_label"] == pts[4]["label"]
    assert s["latest"] == 40 and s["partial"] == 3.0
    assert an.summarize(weekly(lambda d: 0, weeks=10), 0)["has_data"] is False


def test_momentum_labels():
    end = date(2026, 9, 27)
    rising = an.momentum(weekly(lambda d: 100 - (end - d).days * 0.04), 0)
    assert rising["label"] == "rising" and rising["yoy"] > 15 and rising["slope"] > 0
    fading = an.momentum(weekly(lambda d: 20 + (end - d).days * 0.04), 0)
    assert fading["label"] == "declining" and fading["yoy"] < -15
    flat = an.momentum(weekly(lambda d: 50), 0)
    assert flat["label"] == "stable" and abs(flat["yoy"]) < 1e-9
    new = an.momentum(weekly(lambda d: 60 if (end - d).days < 200 else 0), 0)
    assert new["label"] == "new"
    boom = an.momentum(weekly(lambda d: 90 if (end - d).days < 90 else 10), 0)
    assert boom["label"] == "breakout"
    assert an.momentum(weekly(lambda d: 0), 0)["label"] == "no data"
    assert an.momentum(weekly(lambda d: 50, weeks=5), 0)["label"] == "too little data"  # interest, but too few points to judge


def test_a_seasonal_term_is_not_called_rising_or_falling():
    m = an.momentum(weekly(summer), 0)
    assert m["label"] == "stable"  # year over year it is the same


def test_seasonality_finds_the_peak_month_and_its_strength():
    res = an.seasonality(an.monthly_means(weekly(summer), 0))
    assert res["ok"] and res["peak_month"] == 7 and res["trough_month"] == 1
    assert res["label"] == "strongly seasonal" and res["consistency"] == 1.0
    assert 6 in res["high_months"] and 12 in res["low_months"]
    assert abs(sum(res["index"].values()) / 12 - 100) < 1e-6


def test_growth_is_not_mistaken_for_seasonality():
    end = date(2026, 9, 27)
    res = an.seasonality(an.monthly_means(weekly(lambda d: 100 - (end - d).days * 0.05), 0))
    assert res["ok"] and res["label"] == "not seasonal" and res["strength"] < 15


def test_seasonality_survives_a_trend_on_top():
    end = date(2026, 9, 27)
    res = an.seasonality(an.monthly_means(weekly(lambda d: summer(d) * (2 - (end - d).days / 1900)), 0))
    assert res["peak_month"] == 7 and res["label"] == "strongly seasonal"


def test_seasonality_needs_two_years():
    assert an.seasonality(an.monthly_means(weekly(summer, weeks=60), 0))["ok"] is False
    assert an.seasonality(an.monthly_means(weekly(lambda d: 0), 0))["ok"] is False


def test_publish_window_walks_back_from_the_peak():
    idx = an.seasonality(an.monthly_means(weekly(summer), 0))["index"]
    win = an.publish_window(7, idx, lead_weeks=8)
    assert win["peak"] == 7 and win["ramp_start"] in (4, 5) and win["publish_by"] == win["ramp_start"] - 2
    assert an.months_until(3, date(2026, 10, 1)) == 5 and an.months_until(10, date(2026, 10, 1)) == 0


def test_seasonal_outlook_follows_the_index():
    monthly = an.monthly_means(weekly(summer), 0)
    idx = an.seasonality(monthly)["index"]
    out = an.seasonal_outlook(monthly, idx, 12)
    assert len(out) == 12 and out[0][:2] == (2026, 10)
    july = next(v for _, m, v in out if m == 7)
    january = next(v for _, m, v in out if m == 1)
    assert july > 3 * january


def test_find_spikes_dates_and_sizes_them():
    end = date(2026, 9, 27)
    pts = weekly(lambda d: 100 if 189 <= (end - d).days <= 203 else 20)
    spikes = an.find_spikes(pts, 0, threshold=2.0)
    assert len(spikes) == 1
    s = spikes[0]
    assert s["length"] == 3 and abs(s["ratio"] - 5.0) < 1e-6 and not s["ongoing"]
    assert an.find_spikes(weekly(lambda d: 50), 0) == []
    ongoing = an.find_spikes(weekly(lambda d: 90 if (end - d).days < 14 else 10), 0)
    assert ongoing[-1]["ongoing"] is True


def test_share_of_search():
    pts = [{"t": i, "label": str(i), "values": [60.0 + i, 30.0, 10.0], "partial": False} for i in range(20)]
    sos = an.share_of_search(pts, 3)
    assert abs(sum(sos["overall"]) - 100) < 1e-9
    assert sos["end"][0] > sos["start"][0] and sos["end"][1] < sos["start"][1]
    assert len(sos["series"]) == 20
    assert an.share_of_search([], 2)["overall"] == [0.0, 0.0]


def test_rescale_batches_puts_groups_on_one_scale():
    # true sizes: a=80, b=40, anchor=20, c=10, d=5 — each batch scaled to its own max, as Google does
    b1 = {"labels": ["a", "b", "anchor"], "averages": [100.0, 50.0, 25.0], "peaks": [100.0, 60.0, 30.0], "latest": [90.0, 50.0, 25.0]}
    b2 = {"labels": ["anchor", "c", "d"], "averages": [100.0, 50.0, 25.0], "peaks": [100.0, 50.0, 25.0], "latest": [100.0, 50.0, 25.0]}
    res = an.rescale_batches([b1, b2], "anchor")
    got = {r["label"]: round(r["average"], 2) for r in res["rows"]}
    assert got == {"a": 100.0, "b": 50.0, "anchor": 25.0, "c": 12.5, "d": 6.25}
    assert [r["label"] for r in res["rows"]] == ["a", "b", "anchor", "c", "d"]
    assert res["unplaced"] == []


def test_rescale_reports_groups_it_cannot_place():
    b1 = {"labels": ["a", "anchor"], "averages": [100.0, 10.0], "peaks": [100.0, 10.0], "latest": [100.0, 10.0]}
    b2 = {"labels": ["anchor", "giant"], "averages": [0.0, 100.0], "peaks": [0.0, 100.0], "latest": [0.0, 100.0]}
    res = an.rescale_batches([b1, b2], "anchor")
    assert res["unplaced"] == ["giant"] and {r["label"] for r in res["rows"]} == {"a", "anchor"}


def test_stitch_recovers_one_scale_from_overlapping_windows():
    truth = {date(2025, 1, 1) + timedelta(days=i): 10.0 + i for i in range(100)}  # 10 → 109
    days = sorted(truth)

    def window(a, b):  # each window is scaled to its own max, as Google does
        top = max(truth[d] for d in days[a:b])
        return [(d, truth[d] / top * 100) for d in days[a:b]]

    series, warnings = an.stitch([window(0, 60), window(40, 100)])
    assert warnings == [] and len(series) == 100
    assert abs(series[-1][1] - 100) < 1e-6
    assert abs(series[0][1] - 10.0 / 109.0 * 100) < 0.01


def test_stitch_warns_when_windows_share_nothing_measurable():
    a = [(date(2025, 1, 1) + timedelta(days=i), 0.0) for i in range(10)]
    b = [(date(2025, 1, 6) + timedelta(days=i), 50.0) for i in range(10)]
    _, warnings = an.stitch([a, b])
    assert len(warnings) == 1


def test_stitch_warns_when_the_overlap_is_too_faint_to_scale_by():
    a = [(date(2025, 1, 1) + timedelta(days=i), 100.0 if i < 5 else 1.0) for i in range(20)]  # a spike, then a trace
    b = [(date(2025, 1, 11) + timedelta(days=i), 1.0 if i < 10 else 50.0) for i in range(20)]
    _, warnings = an.stitch([a, b])
    assert len(warnings) == 1 and "approximate" in warnings[0]


def test_weekday_profile_and_weekly_means():
    series = [(date(2026, 6, 1) + timedelta(days=i), 80.0 if (date(2026, 6, 1) + timedelta(days=i)).weekday() >= 5 else 40.0) for i in range(28)]
    wd = an.weekday_profile(series)
    assert wd[5] == wd[6] > 100 > wd[0]
    weeks = an.weekly_from_daily(series)
    assert len(weeks) == 4 and all(w[0].weekday() == 0 for w in weeks)
