"""Shared state for the tools: settings, the client, the store, and input parsing.

Tools never build requests themselves. They hand the runtime what the user
typed — keywords, a time range, a location — and get back parsed values or
data, with defaults from the environment filled in.
"""

from __future__ import annotations

import functools
import re
import sqlite3
import threading
from collections.abc import Callable

from .client import PROPERTY_LABELS, TrendsClient, TrendsError, normalize_geo, normalize_property
from .format import safe
from .geo import category_name, geo_name
from .settings import Settings
from .store import Store
from .timeframes import Timeframe, parse_timeframe

try:  # MCP SDK 2.x
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - MCP SDK 1.x
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

__all__ = ["Runtime", "ToolError", "hints"]

MAX_OUTPUT = 60_000  # characters a tool may return

_TOPIC_ID = re.compile(r"^/[mg]/[\w]+$")


def hints(read_only: bool = True, destructive: bool = False, idempotent: bool = True, open_world: bool = True):
    """Tool annotations, so a client can tell a read from a write before asking the user."""
    try:
        from mcp.types import ToolAnnotations

        return ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive, idempotent_hint=idempotent, open_world_hint=open_world)
    except Exception:  # pragma: no cover - older SDK without these fields
        return None


class Runtime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self._client: TrendsClient | None = None
        self._store: Store | None = None
        # Tools run on worker threads; without the lock two first calls race to create the database.
        self._lock = threading.RLock()

    # -- singletons ----------------------------------------------------------

    def store(self) -> Store:
        with self._lock:
            if self._store is None:
                self._store = Store(self.settings.db_path)
            return self._store

    def client(self) -> TrendsClient:
        with self._lock:
            if self._client is None:
                self._client = TrendsClient(self.settings, self.store())
            return self._client

    def tz(self):
        """The timezone for displayed times; ``None`` means this machine's local time."""
        return self.settings.tzinfo()

    # -- what the user typed ---------------------------------------------------

    def geo(self, given: str | None) -> str:
        """A location code; blank means the configured default (worldwide unless ``GTRENDS_GEO`` is set)."""
        if given is None or not str(given).strip():
            return self.settings.geo
        return normalize_geo(given)

    def country(self, given: str | None) -> str:
        """A location for Trending Now, which has no worldwide list: the given one, the default, else US."""
        return self.geo(given) or "US"

    def geos(self, given: str | None, default: list[str], cap: int) -> list[str]:
        raw = [g for g in re.split(r"[,\s;]+", given or "") if g.strip()]
        codes = list(dict.fromkeys(normalize_geo(g) for g in raw)) if raw else default
        codes = [c for c in codes if c]
        if not codes:
            raise ValueError("Give at least one country or region code (US, GB, DE, IR …).")
        if len(codes) > cap:
            raise ValueError(f"At most {cap} locations per call.")
        return codes

    @staticmethod
    def keywords(text: str | None, cap: int | None = None, minimum: int = 1) -> list[str]:
        """Terms separated by commas or new lines, in order, without repeats."""
        parts = [" ".join(p.split()) for p in re.split(r"[,\n;]+", text or "")]
        out = list(dict.fromkeys(p for p in parts if p))
        if len(out) < minimum:
            raise ValueError("Give at least one keyword." if minimum == 1 else f"Give at least {minimum} keywords, separated by commas.")
        if cap is not None and len(out) > cap:
            raise ValueError(f"At most {cap} keywords per call here (got {len(out)}).")
        for k in out:
            if len(k) > 100:
                raise ValueError(f"A search term can be at most 100 characters: {k[:40]}…")
        return out

    @staticmethod
    def hours(value: int) -> int:
        """Trending Now's look-back window, kept inside what Google serves (1 to 191 hours)."""
        return max(1, min(int(value), 191))

    @staticmethod
    def timeframe(text: str | None, default: str = "12m") -> Timeframe:
        """The time range asked for; blank means the tool's own default, not a global one."""
        return parse_timeframe(text if text and text.strip() else default)

    @staticmethod
    def lead_weeks(value: int) -> int:
        if not 0 <= int(value) <= 26:
            raise ValueError("lead_weeks must be between 0 and 26.")
        return int(value)

    @staticmethod
    def prop(text: str | None) -> str:
        return normalize_property(text)

    @staticmethod
    def is_topic(keyword: str) -> bool:
        return bool(_TOPIC_ID.match(keyword))

    @staticmethod
    def items(keywords: list[str], tf: Timeframe, geo: str) -> list[dict]:
        return [{"keyword": k, "geo": geo, "time": tf.value} for k in keywords]

    # -- names for codes -------------------------------------------------------

    def geo_label(self, code: str) -> str:
        """``United States (US)`` — falls back to the bare code when the location list is unavailable."""
        if not code:
            return "Worldwide"
        try:
            name = geo_name(self.client().geo_tree(), code)
        except TrendsError:
            return code
        return code if name == code else f"{safe(name)} ({code})"  # the name is Google's text, not ours

    def category_label(self, cat_id: int) -> str:
        if not cat_id:
            return ""
        try:
            return safe(category_name(self.client().category_tree(), int(cat_id)))
        except TrendsError:
            return f"category {cat_id}"

    def scope(self, tf: Timeframe | None, geo: str, category: int = 0, prop: str = "") -> str:
        """One line that says what a result covers."""
        parts = [tf.label] if tf else []
        parts.append(self.geo_label(geo))
        if category:
            parts.append(self.category_label(category))
        parts.append(PROPERTY_LABELS.get(prop, prop))
        return " · ".join(parts)

    # -- error handling --------------------------------------------------------

    def guard(self, fn: Callable[..., str]) -> Callable[..., str]:
        """Give a tool call its time budget, append the client's notes, and keep the reason of any failure.

        The MCP SDK reports an exception it does not know as the bare text
        "Error executing tool <name>" — the model then has nothing to correct
        itself with. Raising ``ToolError`` keeps the message and sets
        ``isError``, so clients can tell a failure from data.
        """

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> str:
            try:
                client = self.client()
                client.begin_call()
                out = fn(*args, **kwargs)
                if len(out) > MAX_OUTPUT:  # the last line of defence: no answer may flood the model's context
                    out = out[:MAX_OUTPUT] + f"\n\n_The answer was cut at {MAX_OUTPUT:,} characters ({len(out) - MAX_OUTPUT:,} left out). Narrow the request._"
                notes = client.notes()
                if notes:
                    out += "\n\n" + "\n".join(f"_Note: {n}_" for n in notes)
                return out
            except ToolError:
                raise
            except TrendsError as e:
                raise ToolError(str(e)) from e
            except sqlite3.Error as e:
                raise ToolError(f"SQL error: {e}") from e
            except ValueError as e:
                raise ToolError(str(e)) from e
            except LookupError as e:  # includes an unknown timezone name
                raise ToolError(f"Not found: {e}") from e
            except Exception as e:
                raise ToolError(f"Unexpected {type(e).__name__}: {e}") from e

        return wrapper
