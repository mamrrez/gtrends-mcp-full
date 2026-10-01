from datetime import date

import pytest

from gtrends_mcp_full.timeframes import daily_windows, expected_resolution, parse_timeframe, previous_period, year_before

TODAY = date(2026, 10, 1)


def tf(text):
    return parse_timeframe(text, TODAY)


@pytest.mark.parametrize(
    "text, value",
    [
        ("1h", "now 1-H"),
        ("4h", "now 4-H"),
        ("24h", "now 1-d"),
        ("7d", "now 7-d"),
        ("now 7-d", "now 7-d"),
        ("1m", "today 1-m"),
        ("3m", "today 3-m"),
        ("12m", "today 12-m"),
        ("1y", "today 12-m"),
        ("TODAY 12-M", "today 12-m"),
        ("past year", "today 12-m"),
        ("5y", "today 5-y"),
        ("all", "all"),
        (None, "today 12-m"),
    ],
)
def test_presets_map_to_what_google_accepts(text, value):
    assert tf(text).value == value


@pytest.mark.parametrize(
    "text, value",
    [
        ("past 24 hours", "now 1-d"),  # the labels this server prints are accepted back
        ("past 4 hours", "now 4-H"),
        ("past 7 days", "now 7-d"),
        ("7 days", "now 7-d"),
        ("1 week", "now 7-d"),
        ("30 days", "today 1-m"),
        ("past 3 months", "today 3-m"),
        ("5 years", "today 5-y"),
        ("2026-09-28T00 TO 2026-09-30T00", "2026-09-28T00 2026-09-30T00"),
    ],
)
def test_spelled_out_ranges_are_the_same_request_as_their_short_form(text, value):
    assert tf(text).value == value


def test_hours_other_than_the_presets_are_refused_with_the_alternatives():
    with pytest.raises(ValueError, match="1h, 4h or 24h"):
        tf("12 hours")


def test_ranges_google_does_not_know_become_explicit_dates():
    # "today 6-m" is answered with HTTP 400, so it must never be sent
    assert tf("6m").value == "2026-04-01 2026-10-01"
    assert tf("2y").value == "2024-10-01 2026-10-01"
    assert tf("45d").value == "2026-08-17 2026-10-01"
    assert tf("last 2 weeks").value == "2026-09-17 2026-10-01"
    assert tf("2024-03-01 2025-03-01").days == 366 and year_before(tf("2024-02-29 2024-03-31")).value == "2023-02-28 2023-03-31"
    assert tf("6m").label == "past 6 months"


def test_years_months_and_explicit_ranges():
    assert tf("2024").value == "2024-01-01 2024-12-31"
    assert tf("2024-02").value == "2024-02-01 2024-02-29"  # leap year
    assert tf("2019-2023").value == "2019-01-01 2023-12-31"
    assert tf("2024-01-01 2024-12-31").days == 366
    assert tf("2024-01-01 to 2024-03-01").value == "2024-01-01 2024-03-01"


def test_a_range_running_into_the_future_is_cut_at_today():
    assert tf("2026").value == "2026-01-01 2026-10-01"


def test_hourly_range():
    t = tf("2026-09-28T00 2026-09-30T00")
    assert t.value == "2026-09-28T00 2026-09-30T00" and t.days == 2
    with pytest.raises(ValueError, match="at most 7 days"):
        tf("2026-09-01T00 2026-09-30T00")


@pytest.mark.parametrize("bad", ["soon", "2024-13", "2024-02-30 2024-03-01", "2025-01-01 2024-01-01", "1999-01-01 2001-01-01", "0d", "2030-01-01 2030-02-01"])
def test_bad_ranges_say_why(bad):
    with pytest.raises(ValueError):
        tf(bad)


def test_a_very_long_relative_range_is_all_of_it():
    assert tf("40y").value == "all"


def test_month_arithmetic_clamps_to_the_last_day():
    assert parse_timeframe("1m", date(2026, 3, 31)).start == date(2026, 2, 28)


def test_resolution_follows_length():
    assert expected_resolution(1 / 24) == "minute"
    assert expected_resolution(7) == "hour"
    assert expected_resolution(90) == "day"
    assert expected_resolution(365) == "week"
    assert expected_resolution(3000) == "month"
    assert tf("5y").resolution == "week"


def test_previous_period_and_year_before():
    t = tf("2026-07-01 2026-09-30")
    assert previous_period(t).value == "2026-03-31 2026-06-30"
    assert year_before(t).value == "2025-07-01 2025-09-30"
    with pytest.raises(ValueError):
        year_before(tf("7d"))
    with pytest.raises(ValueError, match="2004"):
        year_before(tf("2004-06-01 2004-12-01"))


def test_daily_windows_overlap_and_cover_the_range():
    start, end = date(2024, 1, 1), date(2026, 1, 1)
    wins = daily_windows(start, end, window=240, overlap=45)
    assert wins[0][0] == start and wins[-1][1] == end
    for (_, a_end), (b_start, _) in zip(wins, wins[1:]):
        assert (a_end - b_start).days + 1 == 45
    assert all((b - a).days + 1 <= 240 for a, b in wins)
    assert daily_windows(start, date(2024, 3, 1)) == [(start, date(2024, 3, 1))]
