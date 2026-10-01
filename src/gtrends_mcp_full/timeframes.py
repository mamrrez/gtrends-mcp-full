"""Time ranges, the way people say them and the way Google Trends wants them.

Google accepts a handful of presets (``now 7-d``, ``today 12-m``, ``today 5-y``,
``all``) and explicit ranges (``2024-01-01 2024-12-31``). Anything else — "6m",
"2y", "2024", "2024-03" — is turned into an explicit range here, because the
service answers an unknown preset such as ``today 6-m`` with a bare HTTP 400.

The granularity of the answer is Google's choice and follows the length of
the range; :func:`expected_resolution` mirrors that rule so a tool can say what
is coming before it asks.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

EARLIEST = date(2004, 1, 1)

# what people type -> what Google calls it
_PRESETS = {
    "1h": "now 1-H", "hour": "now 1-H", "now 1-h": "now 1-H",
    "4h": "now 4-H", "now 4-h": "now 4-H",
    "1d": "now 1-d", "24h": "now 1-d", "day": "now 1-d", "today": "now 1-d", "now 1-d": "now 1-d",
    "7d": "now 7-d", "1w": "now 7-d", "week": "now 7-d", "now 7-d": "now 7-d",
    "1m": "today 1-m", "30d": "today 1-m", "month": "today 1-m", "today 1-m": "today 1-m",
    "3m": "today 3-m", "90d": "today 3-m", "quarter": "today 3-m", "today 3-m": "today 3-m",
    "12m": "today 12-m", "1y": "today 12-m", "year": "today 12-m", "today 12-m": "today 12-m",
    "5y": "today 5-y", "today 5-y": "today 5-y",
    "all": "all", "max": "all", "2004-present": "all", "alltime": "all", "all time": "all",
}  # fmt: skip

_RELATIVE = re.compile(r"^(\d{1,4})\s*(d|day|days|w|week|weeks|m|mo|month|months|y|yr|year|years)$")
_HOURS = re.compile(r"^(\d{1,3})\s*(?:h|hr|hrs|hour|hours)$")
_DATE = r"\d{4}-\d{2}-\d{2}"
_RANGE = re.compile(rf"^({_DATE})(?:\s+|\s*(?:to|\.\.|–|—|/)\s*)({_DATE})$")
_HOUR_RANGE = re.compile(rf"^({_DATE}T\d{{2}})(?:\s+|\s*(?:to|\.\.)\s*)({_DATE}T\d{{2}})$")
_YEAR = re.compile(r"^(20\d{2})$")
_YEAR_MONTH = re.compile(r"^(20\d{2})-(\d{2})$")
_YEAR_SPAN = re.compile(r"^(20\d{2})\s*(?:-|to|\.\.|–)\s*(20\d{2})$")


@dataclass(frozen=True)
class Timeframe:
    value: str  # what is sent to Google
    label: str  # how to describe it to a person
    start: date | None  # None for the rolling "now …" presets
    end: date | None
    days: float  # approximate length, for resolution and stitching decisions

    @property
    def resolution(self) -> str:
        return expected_resolution(self.days)


def expected_resolution(days: float) -> str:
    """The granularity Google answers with for a range of this length."""
    if days <= 0.17:  # up to 4 hours
        return "minute"
    if days <= 1.01:
        return "8 minutes"
    if days <= 7.05:
        return "hour" if days > 3 else "16 minutes"
    if days < 270:
        return "day"
    if days <= 1900:  # a little over five years
        return "week"
    return "month"


def _shift_months(d: date, months: int) -> date:
    """``d`` moved by a number of months, clamped to the month's last day."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1]))


def _iso(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{s!r} is not a real date (use YYYY-MM-DD)") from None


def _explicit(start: date, end: date, today: date) -> Timeframe:
    if start > end:
        raise ValueError(f"The range starts ({start}) after it ends ({end}).")
    if start < EARLIEST:
        raise ValueError("Google Trends data begins on 2004-01-01.")
    if end > today:
        end = today
        if start > end:
            raise ValueError(f"The range starts in the future ({start}).")
    return Timeframe(f"{start.isoformat()} {end.isoformat()}", f"{start.isoformat()} to {end.isoformat()}", start, end, (end - start).days + 1)


def parse_timeframe(text: str | None, today: date | None = None) -> Timeframe:
    """Understand a time range.

    Accepted: ``1h 4h 1d 7d 1m 3m 12m 5y all`` and Google's own spellings
    (``now 7-d``, ``today 12-m``); any ``<n>d/w/m/y`` (``6m``, ``2y``, ``45d``);
    a year ``2024``; a month ``2024-03``; years ``2019-2023``; an explicit
    ``2024-01-01 2024-12-31``; an hourly ``2026-09-28T00 2026-09-30T00``
    (at most seven days).
    """
    today = today or date.today()
    raw = (text or "12m").strip()
    s = re.sub(r"\s+", " ", raw.lower())
    s = {"past hour": "1h", "past day": "1d", "past week": "7d", "past month": "1m", "past year": "12m"}.get(s, s)
    s = re.sub(r"^(past|last)\s+", "", s)
    m = _HOURS.match(s)
    if m:
        s = f"{int(m.group(1))}h"
        if s not in _PRESETS:
            raise ValueError("Ranges in hours can be 1h, 4h or 24h; for anything else use days (7d) or an hourly range (2026-09-28T00 2026-09-30T00).")
    m = _RELATIVE.match(s)
    if m and f"{int(m.group(1))}{m.group(2)[0]}" in _PRESETS:
        s = f"{int(m.group(1))}{m.group(2)[0]}"  # "7 days" is the same request as "7d", not an eight-day range of dates

    if s in _PRESETS:
        value = _PRESETS[s]
        if value == "all":
            return Timeframe("all", "2004 to today", EARLIEST, today, (today - EARLIEST).days + 1)
        n, unit = re.match(r"^(?:now|today) (\d+)-(\w)$", value).groups()  # type: ignore[union-attr]
        n = int(n)
        if unit == "H":
            return Timeframe(value, f"past {n} hour{'s' if n > 1 else ''}", None, None, n / 24)
        if unit == "d":
            return Timeframe(value, "past 24 hours" if n == 1 else f"past {n} days", None, None, float(n))
        start = _shift_months(today, -n) if unit == "m" else _shift_months(today, -12 * n)
        label = {"today 1-m": "past month", "today 3-m": "past 3 months", "today 12-m": "past 12 months", "today 5-y": "past 5 years"}[value]
        return Timeframe(value, label, start, today, (today - start).days + 1)

    m = _RELATIVE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        if n == 0:
            raise ValueError("The range must be at least one day long.")
        if unit == "d":
            start = today - timedelta(days=n)
        elif unit == "w":
            start = today - timedelta(weeks=n)
        elif unit == "m":
            start = _shift_months(today, -n)
        else:
            start = _shift_months(today, -12 * n)
        if start < EARLIEST:
            return parse_timeframe("all", today)
        tf = _explicit(start, today, today)
        name = {"d": "day", "w": "week", "m": "month", "y": "year"}[unit] + ("s" if n > 1 else "")
        return Timeframe(tf.value, f"past {n} {name}", tf.start, tf.end, tf.days)

    m = _HOUR_RANGE.match(re.sub(r"\s+(?:to|\.\.)\s+|\s+", " ", raw.strip(), flags=re.IGNORECASE).upper())
    if m:
        a, b = (datetime.strptime(x, "%Y-%m-%dT%H") for x in m.groups())
        if a >= b:
            raise ValueError("The hourly range must end after it starts.")
        if (b - a) > timedelta(days=7, hours=1):
            raise ValueError("Hourly ranges can span at most 7 days; use dates (YYYY-MM-DD YYYY-MM-DD) for longer ones.")
        return Timeframe(f"{m.group(1)} {m.group(2)}", f"{m.group(1)}:00 to {m.group(2)}:00", a.date(), b.date(), (b - a).total_seconds() / 86400)

    m = _RANGE.match(s)
    if m:
        return _explicit(_iso(m.group(1)), _iso(m.group(2)), today)

    m = _YEAR.match(s)
    if m:
        y = int(m.group(1))
        tf = _explicit(date(y, 1, 1), date(y, 12, 31), today)
        return Timeframe(tf.value, f"year {y}", tf.start, tf.end, tf.days)

    m = _YEAR_MONTH.match(s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise ValueError(f"{raw!r}: month must be 01-12.")
        tf = _explicit(date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1]), today)
        return Timeframe(tf.value, f"{calendar.month_name[mo]} {y}", tf.start, tf.end, tf.days)

    m = _YEAR_SPAN.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        tf = _explicit(date(a, 1, 1), date(b, 12, 31), today)
        return Timeframe(tf.value, f"{a} to {b}", tf.start, tf.end, tf.days)

    raise ValueError(
        f"Could not understand the time range {raw[:60]!r}{'…' if len(raw) > 60 else ''}. Use 1h, 4h, 1d, 7d, 1m, 3m, 12m, 5y, all, "
        "a number with d/w/m/y (6m, 2y), a year (2024), a month (2024-03) or 'YYYY-MM-DD YYYY-MM-DD'."
    )


def previous_period(tf: Timeframe) -> Timeframe:
    """The range of the same length that ends the day before ``tf`` starts."""
    if tf.start is None or tf.end is None:
        raise ValueError("A rolling range (hours or days back from now) has no fixed previous period; give dates instead.")
    length = (tf.end - tf.start).days
    end = tf.start - timedelta(days=1)
    return _explicit(max(EARLIEST, end - timedelta(days=length)), end, end)


def year_before(tf: Timeframe) -> Timeframe:
    """The same dates one year earlier."""
    if tf.start is None or tf.end is None:
        raise ValueError("A rolling range (hours or days back from now) cannot be compared with last year; give dates instead.")
    start, end = _shift_months(tf.start, -12), _shift_months(tf.end, -12)
    if start < EARLIEST:
        raise ValueError("There is no data a year before this range (Google Trends begins in 2004).")
    return _explicit(start, end, end)


def daily_windows(start: date, end: date, window: int = 240, overlap: int = 45) -> list[tuple[date, date]]:
    """Overlapping windows short enough for daily data, oldest first.

    Google answers ranges under ~270 days day by day. A longer daily series is
    built from several such windows; each must share ``overlap`` days with the
    next so they can be put on one scale.
    """
    if window <= overlap:
        raise ValueError("window must be longer than overlap")
    total = (end - start).days + 1
    if total <= window:
        return [(start, end)]
    out: list[tuple[date, date]] = []
    cursor = start
    while True:
        stop = min(end, cursor + timedelta(days=window - 1))
        out.append((cursor, stop))
        if stop >= end:
            return out
        cursor = stop - timedelta(days=overlap - 1)
