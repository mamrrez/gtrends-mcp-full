"""Compact, readable tool output.

Tools return Markdown text. Rows are capped so a long series cannot flood the
model's context, and the cap is always stated so the model knows to narrow the
question instead of assuming it saw everything.

Text that comes from Google Trends — trend titles, related queries, news
headlines — is typed by the public and is never allowed to act as Markdown.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from urllib.parse import quote

# The backslash is escaped too: without that, text that already contains "\[label\](url)" would come out as a live link.
_ESCAPES = str.maketrans({"\\": "\\\\", "|": "\\|", "[": "\\[", "]": "\\]", "`": "'", "\r": " ", "\n": " "})
_TAG_START = re.compile(r"<(?=[A-Za-z/!?])")  # "<img", "</a", "<!--" — but not the "<" of "<1"


# Characters that have no business in a table cell: control codes (a NUL breaks some clients outright), the
# direction marks and overrides, which can make a row read as something it does not say, and the invisible
# ones — zero-width spaces, line separators, the Unicode "tag" block — that a person cannot see but a model
# still reads. The zero-width joiner and non-joiner stay: Persian and emoji need them.
_UNPRINTABLE = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f\\u061c\\u200b\\u200e\\u200f\\u2028\\u2029\\u202a-\\u202e\\u2060-\\u2064\\u2066-\\u2069\\ufeff\\U000e0000-\\U000e007f]"
)
_URL_UNSAFE = re.compile(r"[\s<>\"'`()\[\]{}|\\^]")


def safe(s: object) -> str:
    """Text from Google or from the caller, made inert for a Markdown table cell."""
    return _TAG_START.sub("&lt;", _UNPRINTABLE.sub(" ", str(s)).translate(_ESCAPES))


def printable(s: object) -> str:
    """Text with the control and invisible characters taken out — for a terminal, where they can do real harm."""
    return _UNPRINTABLE.sub(" ", str(s))


def safe_url(url: object) -> str:
    """A link from Google, fit to print: http(s) only, one line, and nothing in it that Markdown would act on."""
    text = _UNPRINTABLE.sub("", str(url or "")).strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    text = _URL_UNSAFE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group().encode("utf-8")), text)
    return text if len(text) <= 2000 else ""  # no real article link is this long


def short_path(path: object) -> str:
    """A path with the home directory written as ~, so output does not carry the user's account name."""
    text = str(path)
    home = os.path.expanduser("~")
    return "~" + text[len(home) :] if home not in ("", "/") and text.startswith(home) else text


def clip(text: str, limit: int = 80) -> str:
    """Text short enough to quote back: a long input is cut, with a note of how much was left out."""
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({len(text) - limit:,} more characters)"


def fmt_num(x: float, digits: int = 0) -> str:
    if digits == 0:
        return f"{int(round(x)):,}"
    return f"{x:,.{digits}f}"


def fmt_index(x: float) -> str:
    """A 0-100 Trends value: whole numbers, but ``<1`` rather than a misleading 0 for a trace of interest."""
    if 0 < x < 1:
        return "<1"
    return f"{x:.0f}"


def fmt_change(pct: float | None, digits: int = 0) -> str:
    """``+12%`` style change; ``—`` when there is no base to compare against."""
    if pct is None:
        return "—"
    text = f"{pct:,.{digits}f}"
    if float(text.replace(",", "")) == 0:
        return "0%"
    return f"{'+' if pct > 0 else ''}{text}%"


def fmt_points(delta: float) -> str:
    """A change in percentage points: ``+2.4 pts``."""
    text = f"{delta:.1f}"
    if float(text) == 0:
        return "0.0 pts"
    return f"{'+' if delta > 0 else ''}{text} pts"


def fmt_volume(n: int) -> str:
    """Trending Now's bucketed search count: ``200K+``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:g}M+"
    if n >= 1_000:
        return f"{n / 1_000:g}K+"
    return f"{n}+" if n else "—"


def local_dt(ts: int, tz) -> datetime:
    """A timestamp in ``tz``, or in this machine's local time (with its daylight-saving rules) when ``tz`` is None."""
    return datetime.fromtimestamp(int(ts), tz) if tz is not None else datetime.fromtimestamp(int(ts)).astimezone()


def fmt_time(ts: int | None, tz, with_date: bool = True) -> str:
    if not ts:
        return "—"
    return local_dt(ts, tz).strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M")


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} d"


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.1f}"
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"&lt;{len(v):,} bytes of binary data>"
    text = str(v)
    if text.startswith(("http://", "https://")):
        return safe(text) if len(text) <= 2000 else safe(clip(text, 300))  # a real link is never cut: it has to stay usable
    if len(text) > 300:
        text = clip(text, 300)  # no real query or title is this long
    return safe(text)


def md_table(headers: Sequence[str], rows: Iterable[Sequence], max_rows: int | None = None) -> str:
    rows = list(rows)
    shown = rows if max_rows is None else rows[:max_rows]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in shown:
        lines.append("| " + " | ".join(_cell(c) for c in r) + " |")
    if max_rows is not None and len(rows) > max_rows:
        lines.append(f"\n_{len(rows) - max_rows:,} more rows not shown (of {len(rows):,}). Narrow the request or raise `limit`._")
    return "\n".join(lines)


def kv(items: Iterable[tuple[str, object]]) -> str:
    return "\n".join(f"- **{k}:** {v}" for k, v in items)


def explore_url(keywords: Sequence[str], geo: str = "", timeframe: str = "", category: int = 0, prop: str = "") -> str:
    """A link that opens the same question on the Google Trends website."""
    parts = []
    if timeframe and timeframe != "today 12-m":
        parts.append("date=" + quote(timeframe, safe=""))
    if geo:
        parts.append("geo=" + quote(geo, safe=""))
    if category:
        parts.append(f"cat={int(category)}")
    if prop:
        parts.append("gprop=" + quote(prop, safe=""))
    if any(keywords):
        parts.append("q=" + ",".join(quote(k, safe="") for k in keywords))
    return "https://trends.google.com/trends/explore?" + "&".join(parts)


def trending_url(geo: str, hours: int = 24) -> str:
    return f"https://trends.google.com/trending?geo={quote(geo, safe='')}&hours={int(hours)}"
