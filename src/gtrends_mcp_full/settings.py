"""Configuration — environment variables only, read once at start.

Every variable is optional: the server works with none of them set. The cache
and the history database live under ``GTRENDS_CONFIG_DIR``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

APP_NAME = "gtrends-mcp-full"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _path(v: str | None) -> Path | None:
    v = (v or "").strip()
    return Path(os.path.expanduser(v)) if v else None


def _float(name: str, default: float, low: float, high: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    return max(low, min(high, value))


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    db_path: Path
    geo: str  # default location, "" = worldwide
    hl: str  # interface language for names Google returns, e.g. "en-US"
    timezone: str | None  # IANA name; None = this machine's timezone
    proxy: str | None
    cookie: str | None  # raw Cookie header of a signed-in browser session (optional)
    cookies_file: Path | None  # Netscape cookies.txt (optional)
    min_interval: float  # seconds between requests to Google
    cache_ttl: float  # seconds an Explore answer is reused
    trending_ttl: float  # seconds a Trending Now answer is reused
    time_budget: float  # seconds one tool call may spend waiting on Google
    offline: bool  # never touch the network; answer from cache or fail
    user_agent: str | None

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        config_dir = _path(env("GTRENDS_CONFIG_DIR")) or Path.home() / ".config" / APP_NAME
        from .client import normalize_geo  # imported here: client imports this module

        try:
            geo = normalize_geo(env("GTRENDS_GEO"))
        except ValueError:
            raise ValueError(f"GTRENDS_GEO must be a location code such as US, IR or US-CA (or empty for worldwide), got {env('GTRENDS_GEO')!r}") from None
        cookie = (env("GTRENDS_COOKIE") or "").strip() or None
        if cookie and ("\n" in cookie or "\r" in cookie):
            # the value itself is never quoted back: it is a login
            raise ValueError("GTRENDS_COOKIE must be a single line (the value of one Cookie header); it contains a line break.")
        proxy = (env("GTRENDS_PROXY") or "").strip() or None
        if proxy:
            from urllib.parse import urlsplit

            try:
                parts = urlsplit(proxy)
                valid = parts.scheme in ("http", "https", "socks5", "socks5h") and bool(parts.hostname) and (parts.port is None or 0 < parts.port < 65536)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("GTRENDS_PROXY is not a usable proxy URL. Expected http://[user:password@]host:port — characters such as # or @ inside a password must be percent-encoded.")
        return cls(
            config_dir=config_dir,
            db_path=_path(env("GTRENDS_DB_PATH")) or config_dir / "trends.sqlite",
            geo=geo,
            hl=(env("GTRENDS_HL") or "en-US").strip(),
            timezone=(env("GTRENDS_TIMEZONE") or "").strip() or None,
            proxy=proxy,
            cookie=cookie,
            cookies_file=_path(env("GTRENDS_COOKIES_FILE")),
            min_interval=_float("GTRENDS_MIN_INTERVAL", 1.5, 0.0, 60.0),
            cache_ttl=_float("GTRENDS_CACHE_TTL", 3600.0, 0.0, 30 * 86400.0),
            trending_ttl=_float("GTRENDS_TRENDING_TTL", 300.0, 0.0, 86400.0),
            time_budget=_float("GTRENDS_TIME_BUDGET", 50.0, 5.0, 600.0),
            offline=_truthy(env("GTRENDS_OFFLINE")),
            user_agent=(env("GTRENDS_USER_AGENT") or "").strip() or None,
        )

    def tzinfo(self):
        """The configured timezone, or ``None`` for this machine's local time.

        ``None`` rather than the machine's current offset: a fixed offset
        would show a time from the other side of a daylight-saving change an
        hour out. :func:`format.local_dt` turns either into a datetime.
        """
        if self.timezone:
            from zoneinfo import ZoneInfo

            return ZoneInfo(self.timezone)
        return None

    def tz_label(self) -> str:
        """``Asia/Tehran`` when configured, otherwise this machine's offset as ``UTC+03:30``."""
        if self.timezone:
            return self.timezone
        minutes = -self.tz_offset_minutes()
        if minutes == 0:
            return "UTC"
        sign = "+" if minutes > 0 else "-"
        return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"

    def tz_offset_minutes(self) -> int:
        """Google's ``tz`` parameter: minutes *behind* UTC, as JavaScript's getTimezoneOffset (Tehran = -210)."""
        tz = self.tzinfo()
        offset = (datetime.now(tz) if tz else datetime.now().astimezone()).utcoffset()
        return -int(offset.total_seconds() // 60) if offset is not None else 0
