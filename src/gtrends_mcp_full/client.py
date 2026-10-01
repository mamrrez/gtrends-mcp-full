"""The Google Trends client: plain HTTPS, no API key, no browser.

It talks to the same endpoints the Trends website uses:

- ``/trends/api/explore`` hands out one signed token per chart ("widget") and
  ``/trends/api/widgetdata/*`` trades a token for that chart's data;
- ``/_/TrendsUi/data/batchexecute`` serves the Trending Now page;
- ``/trending/rss`` is the public feed;
- ``/trends/api/autocomplete`` and ``/trends/api/explore/pickers/*`` resolve
  topics, locations and categories.

Three things keep it working where naive scripts get blocked:

1. **The session cookie.** The Explore endpoints refuse a request without
   Google's ``NID`` cookie (HTTP 429) and hand one out with the refusal — but a
   cookie that new is itself turned away for about a minute and a half on a
   network Google is wary of. The client keeps one cookie, lets it mature,
   stores it on disk so later runs start with a trusted one, and sends no
   cookie at all to the endpoints that work without.
2. **Pacing.** Requests are spaced out, a 429 backs off instead of hammering,
   and after a hard block the client stops asking that group of endpoints
   for a while.
3. **The cache.** Every answer is stored; an expired answer is still served,
   and labelled, when Google will not give a fresh one.

None of these endpoints is a documented API. Google can change them at any
time; ``check_endpoints`` exists to say which ones still answer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import threading
import time
import warnings
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .format import clip
from .settings import Settings
from .store import Store

BASE = "https://trends.google.com"
EXPLORE_URL = f"{BASE}/trends/api/explore"
WIDGET_URL = f"{BASE}/trends/api/widgetdata"
AUTOCOMPLETE_URL = f"{BASE}/trends/api/autocomplete/"
GEO_PICKER_URL = f"{BASE}/trends/api/explore/pickers/geo"
CATEGORY_PICKER_URL = f"{BASE}/trends/api/explore/pickers/category"
BATCH_URL = f"{BASE}/_/TrendsUi/data/batchexecute"
RSS_URL = f"{BASE}/trending/rss"
REFERER = f"{BASE}/trends/explore"

RPC_TRENDING_NOW = "i0OFE"
RPC_TREND_NEWS = "w4opAf"

DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

MAX_COMPARE = 5  # Google rejects a sixth comparison item with HTTP 400
BREAKOUT = 5000.0  # growth, in percent, above which Google calls a rising search a "Breakout"
RELATED_SPACING = 6.0  # seconds between two related-searches requests
CHART_EXTRA = 0.5  # the chart endpoints are refused more readily than the rest: they get this much more room
SLOW_FACTOR, SLOW_MAX, SLOW_FOR = 1.5, 3.0, 300.0  # after a refusal: stretch the pace, up to 3×, for five minutes
COOKIE_MATURITY = 100.0  # seconds before Google accepts a session cookie it has just issued
COOKIE_MAX_AGE = 150 * 86400.0  # a stored cookie older than this is dropped and replaced

PROPERTIES = {
    "": "", "web": "", "search": "",
    "youtube": "youtube", "yt": "youtube",
    "news": "news",
    "images": "images", "image": "images",
    "shopping": "froogle", "froogle": "froogle",
}  # fmt: skip
PROPERTY_LABELS = {"": "Web Search", "youtube": "YouTube Search", "news": "News Search", "images": "Image Search", "froogle": "Google Shopping"}

# The category ids used by the Trending Now page (not the Explore categories).
TREND_CATEGORIES = {
    1: "Autos and Vehicles", 2: "Beauty and Fashion", 3: "Business and Finance", 4: "Entertainment",
    5: "Food and Drink", 6: "Games", 7: "Health", 8: "Hobbies and Leisure", 9: "Jobs and Education",
    10: "Law and Government", 11: "Other", 13: "Pets and Animals", 14: "Politics", 15: "Science",
    16: "Shopping", 17: "Sports", 18: "Technology", 19: "Travel and Transportation", 20: "Climate",
}  # fmt: skip

GEO_RESOLUTIONS = {"country": "COUNTRY", "region": "REGION", "city": "CITY", "dma": "DMA", "metro": "DMA"}

_RSS_NS = {"ht": "https://trends.google.com/trending/rss"}


class TrendsError(Exception):
    """Google Trends could not answer. The message says why and what to try."""


class Interrupted(TrendsError):
    """Google could not be asked right now. What was fetched before this is still good.

    Tools that make several requests catch this, stop, and report what they
    have together with what is missing.
    """


class RateLimited(Interrupted):
    """Google answered HTTP 429 and backing off did not help."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


class OutOfTime(Interrupted):
    """The tool call's time budget ran out before the next request could be sent."""


class Offline(TrendsError):
    """The server is in offline mode and the answer is not in the cache."""


def strip_xssi(text: str) -> Any:
    """Parse a response that starts with Google's anti-hijacking prefix ``)]}'``."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise TrendsError("Google Trends sent an answer that is not JSON; the endpoint may have changed.")
    try:
        return json.loads(text[start:])
    except ValueError as e:
        raise TrendsError(f"Google Trends sent JSON that could not be parsed ({e}); the endpoint may have changed.") from e


def parse_batch(text: str, rpc: str) -> Any:
    """Pull one RPC's payload out of a ``batchexecute`` envelope."""
    for line in text.splitlines():
        if not line.startswith("[["):
            continue
        try:
            outer = json.loads(line)
        except ValueError:
            continue
        for entry in outer:
            if isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and entry[1] == rpc:
                if not entry[2]:
                    raise TrendsError("Google returned no data for this Trending Now request; the location may not be covered.")
                return json.loads(entry[2])
            if isinstance(entry, list) and entry and entry[0] == "er":
                raise TrendsError("Google Trends rejected the Trending Now request; its format may have changed.")
    raise TrendsError("The Trending Now answer had no data in it; the endpoint may have changed.")


def normalize_property(prop: str | None) -> str:
    key = (prop or "").strip().lower().replace("google ", "").replace(" search", "")
    if key not in PROPERTIES:
        raise ValueError("property must be one of: web, youtube, news, images, shopping")
    return PROPERTIES[key]


def check_category(category: int) -> int:
    """A category id is a non-negative whole number; anything else is refused before Google is asked."""
    try:
        value = int(category)
    except (TypeError, ValueError):
        raise ValueError("category must be a category id (a whole number from find_category), or 0 for all categories.") from None
    if not 0 <= value < 100_000:
        raise ValueError(f"{value} is not a category id. Use find_category to look one up, or 0 for all categories.")
    return value


def normalize_geo(geo: str | None) -> str:
    """``""`` for worldwide, otherwise an upper-case code such as ``IR`` or ``US-CA``."""
    g = (geo or "").strip()
    if g.lower() in ("", "world", "worldwide", "global", "all"):
        return ""
    g = g.upper().replace("_", "-")
    if not re.fullmatch(r"[A-Z]{2}(-[A-Z0-9]{1,3})?(-\d{1,6})?", g):
        raise ValueError(f"{clip(str(geo))!r} is not a location code. Use a country (IR, US, DE), a region (US-CA) or call find_location.")
    return g


class TrendsClient:
    def __init__(self, settings: Settings, store: Store, http: Any | None = None, sleep=time.sleep, clock=time.monotonic, now=time.time):
        self.settings = settings
        self.store = store
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._lock = threading.RLock()
        self._last_request = 0.0
        self._last_in_lane: dict[str, float] = {}  # some endpoints need more room than the general pace
        self._blocked: dict[str, float] = {}  # endpoint family -> monotonic time before which nothing is sent
        self._slow: dict[str, tuple[float, float]] = {}  # endpoint family -> (pace multiplier, until when)
        self.refusals = 0  # rate-limit answers (HTTP 429) from Google since start
        self._local = threading.local()
        self._cookie_path = Path(settings.config_dir) / "cookies.json"
        self._cookie: dict | None = None  # {"value", "issued", "proven"} — our own anonymous session
        self._cookie_loaded = False
        self._own_cookie_header: str | None = None  # a signed-in session supplied by the user
        self.cookie_problem: str | None = None  # why a supplied signed-in cookie is not in use, if it is not
        self.user_type: str | None = None  # how Google classified the last Explore session
        self.requests_sent = 0
        self.cache_hits = 0

    # -- per-call state -------------------------------------------------------

    def begin_call(self, budget: float | None = None) -> None:
        """Start a tool call: reset its notes and give it a time budget."""
        self._local.deadline = self._clock() + (budget if budget is not None else self.settings.time_budget)
        self._local.notes = []

    def notes(self) -> list[str]:
        return list(dict.fromkeys(getattr(self._local, "notes", [])))

    def _note(self, text: str) -> None:
        if not hasattr(self._local, "notes"):
            self._local.notes = []
        self._local.notes.append(text)

    def _remaining(self) -> float:
        deadline = getattr(self._local, "deadline", None)
        return float("inf") if deadline is None else deadline - self._clock()

    def blocked_for(self, family: str | None = None) -> float:
        """Seconds until requests are sent again (0 when not backing off); the longest wait when no family is given."""
        with self._lock:
            now = self._clock()
            waits = [until - now for fam, until in self._blocked.items() if family in (None, fam)]
        return max(0.0, max(waits, default=0.0))

    def pace_factor(self, family: str) -> float:
        """How much wider than usual the gap between requests to this group is right now (1.0 = usual)."""
        with self._lock:
            factor, until = self._slow.get(family, (1.0, 0.0))
            return factor if self._clock() < until else 1.0

    # -- the session ------------------------------------------------------------

    def _session(self):
        if self._http is None:
            import requests

            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": self.settings.user_agent or DEFAULT_UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": f"{self.settings.hl},{self.settings.hl.split('-')[0]};q=0.9,en;q=0.8",
                    "Referer": REFERER,
                }
            )
            if self.settings.proxy:
                s.proxies.update({"http": self.settings.proxy, "https": self.settings.proxy})
            # The cookie is managed here, not by the HTTP library: it must never send one that is being held back.
            from http.cookiejar import DefaultCookiePolicy

            s.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
            self._http = s
        if not self._cookie_loaded:
            self._cookie_loaded = True
            self._load_cookie()
        return self._http

    def _load_cookie(self) -> None:
        st = self.settings
        if st.cookie:  # a signed-in browser session, given as a raw Cookie header
            self._own_cookie_header = st.cookie
            return
        if st.cookies_file:
            from http.cookiejar import MozillaCookieJar

            try:
                moz = MozillaCookieJar(str(st.cookies_file))
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # the parser reports a bad line as a warning with a traceback
                    moz.load(ignore_discard=True, ignore_expires=True)
                pairs = [f"{c.name}={c.value}" for c in moz if c.domain.lstrip(".").endswith("google.com")]
                if pairs:
                    self._own_cookie_header = "; ".join(pairs)
                    return
                self.cookie_problem = "GTRENDS_COOKIES_FILE has no google.com cookies in it"
            except Exception as e:  # a broken cookie file must not stop the server
                # Only the kind of error: the message of a cookie parser quotes the line it choked on.
                self.cookie_problem = f"GTRENDS_COOKIES_FILE could not be read ({type(e).__name__})"
        with contextlib.suppress(Exception):
            data = json.loads(self._cookie_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("value"):
                cookie = {"value": str(data["value"]), "issued": float(data.get("issued", 0)), "proven": bool(data.get("proven"))}
                if self._now() - cookie["issued"] < COOKIE_MAX_AGE:  # Google expires them after about six months
                    self._cookie = cookie

    def _save_cookie(self) -> None:
        if self._cookie is None:
            return
        with contextlib.suppress(Exception):
            self._cookie_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # created owner-only from the first byte, not world-readable for a moment and then tightened
            fd = os.open(self._cookie_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self._cookie))
            os.chmod(self._cookie_path, 0o600)

    def redact(self, text: str) -> str:
        """``text`` with every credential this client holds taken out.

        Error messages from the HTTP library quote what they choked on — a
        malformed Cookie header, a proxy URL with its password. Whatever
        reaches a tool result or a log goes through here first.
        """
        secrets = [self._own_cookie_header, self.settings.cookie, self.settings.proxy]
        if self._cookie:
            secrets.append(self._cookie.get("value"))
        for header in (self._own_cookie_header, self.settings.cookie):
            for pair in (header or "").split(";"):
                secrets.append(pair.strip())
                secrets.append(pair.partition("=")[2].strip())
        if self.settings.proxy:
            from urllib.parse import urlsplit

            with contextlib.suppress(ValueError):
                parts = urlsplit(self.settings.proxy)
                secrets += [parts.password, parts.netloc]
        for secret in sorted({x for x in secrets if x and len(x) >= 4}, key=len, reverse=True):
            text = text.replace(secret, "[hidden]")
        return re.sub(r"//[^/@\s]+@", "//[hidden]@", text)  # any user:password@ that is left

    def _cookie_age(self) -> float:
        return self._now() - self._cookie["issued"] if self._cookie else 0.0

    def _cookie_header(self, needs_cookie: bool) -> str | None:
        """The Cookie header to send, if any.

        Google does not accept a session cookie it has only just issued: for
        about a minute and a half, requests carrying it are answered with a
        challenge page. So a cookie is sent everywhere only once a chart
        request has succeeded with it. Until then it goes only to the
        endpoints that require one — at once the first time (an unflagged
        network is let through), and after a refusal not before it has matured.
        """
        if self._own_cookie_header:
            return self._own_cookie_header
        c = self._cookie
        if c is None:
            return None
        if c["proven"]:
            return f"NID={c['value']}"
        if needs_cookie and (not c.get("rejected") or self._cookie_age() >= COOKIE_MATURITY):
            return f"NID={c['value']}"
        return None

    def _adopt_cookie(self, resp: Any, sent_ours: bool) -> bool:
        """Keep the session cookie Google hands out. Returns True when a new one was taken.

        Only the first cookie is kept, so that it can mature — except when
        Google refuses a request that carried our established cookie and
        issues another: then the old one is no longer honoured and is replaced.
        """
        if self._own_cookie_header:
            return False
        value = None
        with contextlib.suppress(Exception):
            value = resp.cookies.get("NID")
        if not value:
            return False
        c = self._cookie
        if c is not None:
            established = c["proven"] or self._cookie_age() >= COOKIE_MATURITY
            if not (sent_ours and resp.status_code == 429 and established and value != c["value"]):
                return False
        self._cookie = {"value": value, "issued": self._now(), "proven": False}
        self._save_cookie()
        return True

    def session_ready_in(self) -> float:
        """Seconds until a new session cookie is old enough for Google to accept (0 when ready)."""
        c = self._cookie
        if self._own_cookie_header or c is None or c["proven"]:
            return 0.0
        return max(0.0, COOKIE_MATURITY - self._cookie_age())

    def prewarm(self) -> None:
        """Get a session cookie early, so it has matured by the time a chart is asked for. Never raises."""
        with contextlib.suppress(Exception):
            with self._lock:
                self._session()
                if self._own_cookie_header or self._cookie is not None or self.settings.offline:
                    return
            self._request("GET", AUTOCOMPLETE_URL + "a", params=self._params(), what="session warm-up", family="lookup")

    def forget_session(self) -> None:
        """Drop the stored cookie and the back-off state (a fresh start after a block)."""
        with self._lock:
            self._cookie = None
            with contextlib.suppress(OSError):
                self._cookie_path.unlink()
            self._blocked.clear()
            self._slow.clear()

    # -- HTTP -----------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data: str | None = None,
        headers: dict | None = None,
        what: str = "Google Trends",
        spacing: float | None = None,
        family: str = "explore",
        needs_cookie: bool = False,
        lane: str | None = None,
        lane_spacing: float = 0.0,
    ) -> str:
        """One request with pacing, the session cookie and back-off. Returns the body text.

        ``family`` groups endpoints that Google limits together (``explore``,
        ``trending``, ``lookup``): a block on one does not stop the others.
        ``lane`` with ``lane_spacing`` keeps requests to one touchy endpoint
        further apart than the general pace.
        """
        if self.settings.offline:
            raise Offline("Offline mode is on (GTRENDS_OFFLINE=1) and this answer is not in the cache.")
        spacing = self.settings.min_interval if spacing is None else max(spacing, self.settings.min_interval)
        if family == "explore":
            spacing += CHART_EXTRA
        backoffs = [4.0, 9.0, 18.0]
        attempt = 0
        handshakes = 0
        while True:
            step, value = self._attempt(method, url, params, data, headers, what, spacing, family, needs_cookie, lane, lane_spacing)
            if step == "ok":
                return value
            if step == "again":
                # One immediate repeat is the cookie handshake. A second refusal is a rate limit like any other.
                handshakes += 1
                if handshakes == 1:
                    continue
                step, value = "limited", 0.0
            if step == "wait":
                self._sleep(value)
                continue
            if step == "limited":
                retry_after = value
                with self._lock:
                    # Google has just said "slower": keep a wider gap on this group of endpoints for a while,
                    # so the next few requests do not walk into the same refusal.
                    self.refusals += 1
                    self._slow[family] = (min(self.pace_factor(family) * SLOW_FACTOR, SLOW_MAX), self._clock() + SLOW_FOR)
                delay = max(retry_after, backoffs[min(attempt, len(backoffs) - 1)]) + random.uniform(0.0, 1.5)
                attempt += 1
                if attempt > len(backoffs) or delay > self._remaining() - 2.0:
                    # Stop asking this group of endpoints for a while: more requests now only lengthen the block.
                    # A single refusal with no time left to retry earns a short pause, repeated refusals a long one.
                    pause = max(retry_after, 15.0 if attempt == 1 else 60.0 * min(attempt, 5))
                    with self._lock:
                        self._blocked[family] = self._clock() + pause
                    raise RateLimited(self._rate_message(what, pause), retry_after=pause)
                self._sleep(delay)
                continue
            status = value
            if status == 400:
                raise TrendsError(
                    f"Google Trends rejected the request for {what} (HTTP 400). The usual causes: an unknown location code, "
                    "a category id that does not exist, more than 5 terms, or a time range it does not accept."
                )
            if status in (401, 403):
                raise TrendsError(f"Google Trends refused the request for {what} (HTTP {status}). A proxy or signed-in cookie may be blocked or expired.")
            if status == 404:
                raise TrendsError(f"Google Trends has no such endpoint any more ({what}, HTTP 404). Run check_endpoints and update the package.")
            if 300 <= status < 400:
                raise TrendsError(f"Google Trends redirected the request for {what} elsewhere (HTTP {status}); it was not followed. The endpoint may have moved: run check_endpoints and update the package.")
            if status >= 500 and attempt < 2 and self._remaining() > 6.0:
                attempt += 1
                self._sleep(2.0 * attempt)
                continue
            raise TrendsError(f"Google Trends answered HTTP {status} for {what}.")

    def _attempt(self, method, url, params, data, headers, what, spacing, family, needs_cookie, lane=None, lane_spacing=0.0) -> tuple[str, Any]:
        """Send one request. Returns what to do next:

        ``("ok", text)`` · ``("again", None)`` ask again at once · ``("wait", seconds)`` sleep, then ask again ·
        ``("limited", retry_after)`` a real rate limit · ``("status", code)`` any other HTTP status.

        The lock is held only to decide and to record; the waiting and the
        request itself happen outside it, so one slow call cannot stall the
        other tools. Pacing works by reservation: each caller books the next
        free moment and sleeps until then.
        """
        with self._lock:
            http = self._session()
            blocked = self.blocked_for(family)
            if blocked > 0:
                raise RateLimited(self._rate_message(what, blocked), retry_after=blocked)
            cookie = self._cookie_header(needs_cookie)
            if needs_cookie and cookie is None and self._cookie is not None:
                # A new cookie that Google has already turned away once: it has to mature first.
                maturing = self.session_ready_in()
                if maturing > self._remaining() - 3.0:
                    raise RateLimited(self._warming_message(what, maturing), retry_after=maturing)
                return "wait", maturing
            now = self._clock()
            stretch = self.pace_factor(family)
            at = max(now, self._last_request + spacing * stretch)
            if lane and lane in self._last_in_lane:
                at = max(at, self._last_in_lane[lane] + lane_spacing * stretch)
            if at - now > self._remaining():
                raise OutOfTime(f"Ran out of time before {what} could be asked. Ask for less in one call, or raise GTRENDS_TIME_BUDGET.")
            self._last_request = at
            if lane:
                self._last_in_lane[lane] = at
            sent_ours = self._cookie is not None and cookie == f"NID={self._cookie['value']}"
        if at > now:
            self._sleep(at - now)
        send_headers = dict(headers or {})
        if cookie:
            send_headers["Cookie"] = cookie
        try:
            # Redirects are not followed: nothing this client asks for lives anywhere else, and a session
            # cookie must never travel to a host it was not meant for.
            resp = http.request(method, url, params=params, data=data, headers=send_headers or None, timeout=(10, 30), allow_redirects=False)
        except Exception as e:
            raise TrendsError(f"Could not reach Google Trends ({type(e).__name__}: {self.redact(str(e))[:300]}). Check the connection or GTRENDS_PROXY.") from None
        finally:
            with contextlib.suppress(Exception):
                http.cookies.clear()  # belt and braces: the cookie policy above already keeps the jar empty
        with self._lock:
            self.requests_sent += 1
            status = resp.status_code
            c = self._cookie
            still_ours = sent_ours and c is not None and cookie == f"NID={c['value']}"  # another thread may have replaced it
            adopted = self._adopt_cookie(resp, still_ours)
            if status == 200:
                if still_ours and not adopted and not c["proven"] and needs_cookie:
                    c["proven"] = True
                    c.pop("rejected", None)
                    self._save_cookie()
                return "ok", resp.text
            if status in (301, 302, 303, 307, 308) and "/sorry" in str(resp.headers.get("Location", "")):
                status = 429  # the "unusual traffic" page: a rate limit by another name
            if status != 429:
                return "status", status
            if adopted:
                # Not a rate limit: a request without a session cookie, or with one Google no longer
                # honours, is refused and handed a new one. Ask again with it.
                return "again", None
            if still_ours and not c["proven"]:
                # The cookie is too new for Google. Hold it back until it has matured.
                if not c.get("rejected"):
                    c["rejected"] = True
                    self._save_cookie()
                maturing = self.session_ready_in()
                if maturing > 0:
                    if maturing > self._remaining() - 3.0:
                        raise RateLimited(self._warming_message(what, maturing), retry_after=maturing)
                    return "wait", maturing
            return "limited", _retry_after(resp)

    def _rate_message(self, what: str, seconds: float) -> str:
        mins = max(1, round(seconds / 60))
        return (
            f"Google Trends is rate-limiting this connection (HTTP 429) for {what}. The server will not send this kind of request for about "
            f"{mins} minute{'s' if mins != 1 else ''} so the block can clear; answers already in the cache still work. "
            "If this happens often: ask for fewer keywords per call, raise GTRENDS_MIN_INTERVAL, or set GTRENDS_PROXY. "
            "If it never clears, clear_cache with reset_session=true starts a new session."
        )

    @staticmethod
    def _warming_message(what: str, seconds: float) -> str:
        return (
            f"Google is still validating this server's new session, so {what} is not available for about {max(1, round(seconds))} more seconds. "
            "This happens once, after the first start or after the session was reset; Trending Now tools work in the meantime. Try again shortly."
        )

    def _cached(self, kind: str, key_parts: Any, ttl: float, fetch, *, fresh: bool = False) -> Any:
        """Answer from the cache, else fetch and store; on failure fall back to an expired answer.

        ``fresh=True`` means exactly that: no cached answer is accepted, not
        even as a fallback. Signed tokens (``explore``) are never served
        stale either — an old token only produces a confusing refusal later.
        """
        key = kind + ":" + hashlib.sha256(json.dumps(key_parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:32]
        if not fresh:
            hit = self.store.cache_get(key)
            if hit is not None:
                self.cache_hits += 1
                return hit[0]
        try:
            try:
                value = fetch()
            except (KeyError, IndexError, TypeError, AttributeError) as e:
                # Google changed the shape of an answer. Say so, instead of leaking a bare KeyError.
                raise TrendsError(f"Google Trends sent an answer in a format this version does not understand ({type(e).__name__}: {e}). Run check_endpoints and update the package.") from e
        except TrendsError as e:
            stale = None if fresh or kind == "explore" else self.store.cache_get(key, allow_stale=True)
            if stale is None:
                raise
            self._note(f"Google did not answer ({_first_sentence(str(e))}); showing the answer saved {_age(stale[1])} ago.")
            return stale[0]
        self.store.cache_put(key, kind, value, ttl)
        return value

    # -- Explore: tokens ------------------------------------------------------

    def _params(self) -> dict:
        return {"hl": self.settings.hl, "tz": str(self.settings.tz_offset_minutes())}

    def explore(self, items: list[dict], category: int = 0, prop: str = "", fresh: bool = False) -> dict:
        """The widget list (one signed token per chart) for a comparison.

        ``items`` are ``{"keyword", "geo", "time"}`` dicts — up to five.
        """
        category = check_category(category)
        if not items:
            raise ValueError("At least one keyword is required.")
        if len(items) > MAX_COMPARE:
            raise ValueError(f"Google Trends compares at most {MAX_COMPARE} terms at once; use compare_many for more.")
        req = {"comparisonItem": items, "category": int(category), "property": prop}

        def fetch() -> dict:
            params = {**self._params(), "req": json.dumps(req, separators=(",", ":"), ensure_ascii=False)}
            data = strip_xssi(self._request("GET", EXPLORE_URL, params=params, what="the explore request", needs_cookie=True))
            widgets = data.get("widgets") if isinstance(data, dict) else None
            if not isinstance(widgets, list):
                raise TrendsError("The explore answer had no charts in it; the endpoint may have changed.")
            return {"widgets": widgets, "keywords": data.get("keywords", [])}

        # Tokens stay valid for a while, so a second chart for the same question reuses them.
        data = self._cached("explore", [req, self.settings.hl, self.settings.tz_offset_minutes()], min(600.0, max(self.settings.cache_ttl, 1.0)), fetch, fresh=fresh)
        for w in data["widgets"]:
            ut = (w.get("request") or {}).get("userConfig", {}).get("userType")
            if ut:
                self.user_type = ut
                break
        return data

    @staticmethod
    def _widget(data: dict, widget_id: str) -> dict | None:
        return next((w for w in data["widgets"] if w.get("id") == widget_id), None)

    def _widget_data(self, widget: dict, path: str, patch: dict | None = None, what: str = "chart data", lane: str | None = None, lane_spacing: float = 0.0) -> dict:
        req = dict(widget["request"])
        if patch:
            req.update(patch)
        params = {**self._params(), "req": json.dumps(req, separators=(",", ":"), ensure_ascii=False), "token": widget["token"]}
        data = strip_xssi(self._request("GET", f"{WIDGET_URL}/{path}", params=params, what=what, needs_cookie=True, lane=lane, lane_spacing=lane_spacing))
        return data.get("default", {}) if isinstance(data, dict) else {}

    def _ttl(self, items: list[dict]) -> float:
        """Short ranges change by the minute; long ones barely move."""
        time_value = items[0].get("time", "")
        if time_value.startswith("now 1-H") or time_value.startswith("now 4-H"):
            return min(self.settings.cache_ttl, 120.0)
        if time_value.startswith("now"):
            return min(self.settings.cache_ttl, 900.0)
        return self.settings.cache_ttl

    # -- Explore: charts ------------------------------------------------------

    def interest_over_time(self, items: list[dict], category: int = 0, prop: str = "", fresh: bool = False) -> dict:
        """``{"labels", "points": [{"t", "label", "values", "partial"}], "resolution", "averages", "multirange"}``.

        With one time range for every item the points share timestamps. With
        different ranges per item ("multirange") each point holds one
        timestamp per item, aligned by position in its own range.
        """
        category = check_category(category)

        def fetch() -> dict:
            data = self.explore(items, category, prop, fresh=fresh)
            w = self._widget(data, "TIMESERIES")
            if w is None:
                notes = [x.get("text", {}).get("text", "") if isinstance(x.get("text"), dict) else "" for x in data["widgets"] if x.get("id", "").endswith("_note")]
                if notes or ("" in {i.get("geo") for i in items} and len({i.get("geo") for i in items}) > 1):
                    raise TrendsError(
                        "Google Trends returned no time chart for this combination"
                        + (f" ({notes[0]})" if notes and notes[0] else "")
                        + ". Worldwide cannot be mixed with single countries in one comparison."
                    )
                raise TrendsError("Google Trends returned no time chart for this request; the endpoint may have changed. Run check_endpoints.")
            multirange = w.get("type") == "fe_multi_range_chart"
            body = self._widget_data(w, "multirange" if multirange else "multiline", what="interest over time")
            labels = _labels(data, items)
            n = len(items)
            points = []
            for row in body.get("timelineData", []):
                if multirange:
                    cols = [c if isinstance(c, dict) else {} for c in (row.get("columnData") or [])][:n]
                    cols += [{}] * (n - len(cols))  # a range that ended early still gets its column
                    points.append(
                        {
                            "t": [_int(c.get("time")) for c in cols],
                            "label": [str(c.get("formattedTime") or _utc_label(_int(c.get("time")))) for c in cols],
                            "values": [_index(c.get("value")) for c in cols],
                            "partial": any(c.get("isPartial") for c in cols),
                        }
                    )
                else:
                    stamp = _int(row.get("time"))
                    if stamp <= 0:
                        continue  # a point without a time cannot be placed on the chart
                    raw = row.get("value") or []
                    has = row.get("hasData") or [True] * len(raw)
                    points.append(
                        {
                            "t": stamp,
                            "label": str(row.get("formattedTime") or _utc_label(stamp)),
                            "values": _fit([_index(v) if h else 0.0 for v, h in zip(raw, has)], n),
                            "partial": bool(row.get("isPartial")),
                        }
                    )
            return {
                "labels": labels,
                "points": points,
                "resolution": (w.get("request") or {}).get("resolution", ""),
                "averages": body.get("averages", []),
                "multirange": multirange,
            }

        return self._cached("timeseries", [items, category, prop, self.settings.hl, self.settings.tz_offset_minutes()], self._ttl(items), fetch, fresh=fresh)

    def interest_by_region(self, items: list[dict], category: int = 0, prop: str = "", resolution: str | None = None, low_volume: bool = False, fresh: bool = False) -> dict:
        """``{"labels", "resolution", "rows": [{"code", "name", "values", "lat", "lng"}]}``.

        With several items the values are each region's split between the
        terms (percentages that sum to 100), as on the Trends "compared
        breakdown" map; with one item they are 0-100 relative to the top region.
        """
        category = check_category(category)
        res = None
        if resolution:
            key = resolution.strip().lower()
            if key not in GEO_RESOLUTIONS and key != "auto":
                raise ValueError("resolution must be auto, country, region, city or dma")
            res = GEO_RESOLUTIONS.get(key)

        def fetch() -> dict:
            data = self.explore(items, category, prop, fresh=fresh)
            w = self._widget(data, "GEO_MAP")
            if w is None:
                raise TrendsError("Google Trends returned no map for this combination. Compare terms within one location to get a regional breakdown.")
            patch: dict = {}
            if res:
                patch["resolution"] = res
            if low_volume:
                patch["includeLowSearchVolumeGeos"] = True
            body = self._widget_data(w, "comparedgeo", patch or None, what="interest by region")
            rows = []
            for r in body.get("geoMapData") or []:
                if not isinstance(r, dict) or not r.get("value") or not r.get("geoName"):
                    continue  # a place without a name or a number is not a row
                has = r.get("hasData") or [True]
                if not any(has):
                    continue
                coords = r.get("coordinates") or {}
                rows.append(
                    {
                        "code": r.get("geoCode", ""),
                        "name": r.get("geoName", ""),
                        "values": _fit([_index(v) for v in r["value"]], len(items)),
                        "lat": coords.get("lat"),
                        "lng": coords.get("lng"),
                    }
                )
            return {"labels": _labels(data, items), "resolution": res or (w.get("request") or {}).get("resolution", ""), "rows": rows}

        return self._cached("regions", [items, category, prop, res, low_volume, self.settings.hl], self.settings.cache_ttl, fetch, fresh=fresh)

    def related(self, item: dict, kind: str = "queries", category: int = 0, prop: str = "", fresh: bool = False) -> dict:
        """``{"top": [...], "rising": [...]}`` for one term.

        Each entry is ``{"text", "value", "growth", "breakout", "mid", "type"}``:
        ``value`` is 0-100 for *top*; for *rising* it is the growth in percent
        and ``breakout`` marks growth above 5000%.
        """
        category = check_category(category)
        if kind not in ("queries", "topics"):
            raise ValueError("kind must be 'queries' or 'topics'")
        widget_id = "RELATED_QUERIES" if kind == "queries" else "RELATED_TOPICS"

        def fetch() -> dict:
            data = self.explore([item], category, prop, fresh=fresh)
            w = self._widget(data, widget_id)
            if w is None:
                return {"top": [], "rising": [], "available": False, "user_type": self.user_type}
            # The related-searches endpoint has the smallest quota of all: two calls a few seconds apart are
            # refused, so they are kept well apart from each other (other requests may go in between).
            body = self._widget_data(w, "relatedsearches", what=f"related {kind}", lane="related", lane_spacing=RELATED_SPACING)
            lists = body.get("rankedList", [])
            out: dict = {"top": [], "rising": [], "available": True, "user_type": self.user_type}
            for name, ranked in zip(("top", "rising"), lists):
                for r in ranked.get("rankedKeyword", []):
                    topic = r.get("topic") or {}
                    if not (r.get("query") or topic.get("title")):
                        continue  # an entry without text is nothing to show
                    value = _num(r.get("value"))
                    # Google labels growth above 5000% "Breakout" — in the interface language,
                    # so the number is the reliable signal, not the word.
                    breakout = name == "rising" and value >= BREAKOUT
                    out[name].append(
                        {
                            "text": r.get("query") or topic.get("title") or "",
                            "value": value,
                            "growth": ("Breakout" if breakout else f"+{value:,.0f}%") if name == "rising" else "",
                            "breakout": breakout,
                            "mid": topic.get("mid", ""),
                            "type": topic.get("type", ""),
                        }
                    )
            return out

        return self._cached("related", [item, kind, category, prop, self.settings.hl], self.settings.cache_ttl, fetch, fresh=fresh)

    # -- Trending Now ---------------------------------------------------------

    def _batch(self, rpc: str, payload: list, what: str) -> Any:
        body = "f.req=" + quote(json.dumps([[[rpc, json.dumps(payload, ensure_ascii=False), None, "generic"]]], ensure_ascii=False))
        text = self._request(
            "POST",
            BATCH_URL,
            params={"rpcids": rpc, "source-path": "/trending", "hl": self.settings.hl},
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "Referer": f"{BASE}/trending"},
            what=what,
            family="trending",
        )
        return parse_batch(text, rpc)

    def trending_now(self, geo: str, hours: int = 24, fresh: bool = False) -> list[dict]:
        """Every trend on the Trending Now page for a location.

        Each trend: ``title``, ``geo``, ``started`` / ``ended`` (unix seconds,
        ``ended`` is ``None`` while active), ``volume`` (Google's bucketed
        search count), ``growth`` (percent), ``breakdown`` (the queries inside
        the trend), ``categories`` and ``news_tokens``.
        """
        if not geo:
            raise ValueError("Trending Now needs a country or region code (it has no worldwide list). Try US, GB, DE, IR …")
        hours = max(1, min(int(hours), 191))
        lang = self.settings.hl.split("-")[0]

        def fetch() -> list[dict]:
            data = self._batch(RPC_TRENDING_NOW, [None, None, geo, 0, lang, hours, 1], "Trending Now")
            rows = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
            out = []
            for r in rows:
                try:
                    if not isinstance(r[0], str) or not r[0].strip():
                        continue  # a trend without a title cannot be shown or matched
                    out.append(
                        {
                            "title": r[0],
                            "geo": r[2] or geo,
                            "started": int(r[3][0]) if r[3] else 0,
                            "ended": int(r[4][0]) if r[4] else None,
                            "volume": int(r[6] or 0),
                            "growth": int(r[8] or 0),
                            "breakdown": [q for q in (r[9] if isinstance(r[9], list) else []) if isinstance(q, str)],
                            "categories": [TREND_CATEGORIES.get(c, f"Category {c}") for c in (r[10] if isinstance(r[10], list) else []) if isinstance(c, int)],
                            "news_tokens": r[11] if len(r) > 11 and r[11] else [],
                        }
                    )
                except (IndexError, TypeError, ValueError):
                    continue  # one malformed row must not hide the rest
            if rows and not out:
                raise TrendsError("The Trending Now rows are in a format this version does not understand; update the package.")
            return out

        return self._cached("trending", [geo, hours, lang], self.settings.trending_ttl, fetch, fresh=fresh)

    def trend_news(self, tokens: list, limit: int = 5) -> list[dict]:
        """News articles behind a trend, from the tokens Trending Now returned with it."""
        if not tokens:
            return []
        tokens = tokens[:12]

        def fetch() -> list[dict]:
            data = self._batch(RPC_TREND_NEWS, [tokens, int(limit)], "trend news")
            rows = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else []
            out = []
            for r in rows:
                with contextlib.suppress(IndexError, TypeError):
                    out.append({"title": r[0], "url": r[1], "source": r[2], "time": int(r[3][0]) if r[3] else None, "image": r[4] if len(r) > 4 else ""})
            return out

        return self._cached("news", [tokens, limit], max(self.settings.trending_ttl, 600.0), fetch)

    def trending_rss(self, geo: str, fresh: bool = False) -> list[dict]:
        """The public Trending feed: fewer trends than Trending Now, but each comes with its news."""
        if not geo:
            raise ValueError("The trending feed needs a country code. Try US, GB, DE, IR …")

        def fetch() -> list[dict]:
            text = self._request("GET", RSS_URL, params={"geo": geo}, what="the trending feed", family="trending")
            try:
                root = ET.fromstring(text)
            except ET.ParseError as e:
                raise TrendsError(f"The trending feed is not valid XML ({e}).") from e
            out = []
            for item in root.iter("item"):
                started = None
                with contextlib.suppress(Exception):
                    started = int(parsedate_to_datetime(item.findtext("pubDate") or "").timestamp())
                news = [
                    {
                        "title": n.findtext("ht:news_item_title", "", _RSS_NS),
                        "url": n.findtext("ht:news_item_url", "", _RSS_NS),
                        "source": n.findtext("ht:news_item_source", "", _RSS_NS),
                    }
                    for n in item.findall("ht:news_item", _RSS_NS)
                ]
                title = (item.findtext("title") or "").strip()
                if title:
                    out.append({"title": title, "traffic": item.findtext("ht:approx_traffic", "", _RSS_NS), "started": started, "news": [n for n in news if n["title"]]})
            return out

        return self._cached("rss", [geo], self.settings.trending_ttl, fetch, fresh=fresh)

    # -- lookups --------------------------------------------------------------

    def autocomplete(self, text: str, fresh: bool = False) -> list[dict]:
        """Topics Google knows for a piece of text: ``{"mid", "title", "type"}``."""
        text = text.strip()
        if not text:
            raise ValueError("Give some text to look up.")

        def fetch() -> list[dict]:
            data = strip_xssi(self._request("GET", AUTOCOMPLETE_URL + quote(text, safe=""), params=self._params(), what="topic lookup", spacing=0.5, family="lookup"))
            topics = (data.get("default") or {}).get("topics") if isinstance(data, dict) else None
            return [t for t in (topics or []) if isinstance(t, dict) and t.get("mid") and t.get("title")]

        return self._cached("autocomplete", [text, self.settings.hl], 7 * 86400.0, fetch, fresh=fresh)

    def _tree(self, kind: str, url: str, what: str, fresh: bool) -> dict:
        def fetch() -> dict:
            data = strip_xssi(self._request("GET", url, params=self._params(), what=what, family="lookup"))
            if not isinstance(data, dict) or not isinstance(data.get("children"), list):
                raise TrendsError(f"Google Trends sent {what} in a format this version does not understand. Run check_endpoints and update the package.")
            return data

        return self._cached(kind, [self.settings.hl], 30 * 86400.0, fetch, fresh=fresh)

    def geo_tree(self, fresh: bool = False) -> dict:
        return self._tree("geo", GEO_PICKER_URL, "the location list", fresh)

    def category_tree(self, fresh: bool = False) -> dict:
        return self._tree("category", CATEGORY_PICKER_URL, "the category list", fresh)

    # -- diagnostics ----------------------------------------------------------

    def session_kind(self) -> str:
        """How Google sees this session, in plain words."""
        if self.user_type is None:
            return "unknown until the first Explore request"
        if "LEGIT" in self.user_type:
            return "signed-in user (full access)"
        if "SCRAPER" in self.user_type:
            return "anonymous (related topics are withheld by Google; everything else works)"
        if "OVER_QUOTA" in self.user_type:
            return "anonymous and over quota (expect rate limits)"
        return self.user_type

    def check(self) -> list[tuple[str, bool, str]]:
        """Ask each endpoint one small question, never from the cache. ``(name, ok, detail)`` per endpoint."""
        results: list[tuple[str, bool, str]] = []

        def probe(name: str, fn) -> None:
            try:
                results.append((name, True, fn()))
            except TrendsError as e:
                results.append((name, False, _first_sentence(str(e))))
            except Exception as e:  # a parsing surprise is exactly what this tool is for
                results.append((name, False, f"{type(e).__name__}: {self.redact(str(e))[:200]}"))

        item = {"keyword": "weather", "geo": "US", "time": "today 3-m"}
        probe("topic lookup", lambda: f"{len(self.autocomplete('weather', fresh=True))} topics")
        probe("location list", lambda: f"{len(self.geo_tree(fresh=True).get('children', []))} countries")
        probe("category list", lambda: f"{len(self.category_tree(fresh=True).get('children', []))} top-level categories")
        probe("trending feed", lambda: f"{len(self.trending_rss('US', fresh=True))} trends")
        probe("Trending Now", lambda: f"{len(self.trending_now('US', 24, fresh=True))} trends")
        probe("interest over time", lambda: f"{len(self.interest_over_time([item], fresh=True)['points'])} points")
        probe("interest by region", lambda: f"{len(self.interest_by_region([item], fresh=True)['rows'])} regions")
        probe("related queries", lambda: f"{len(self.related(item, fresh=True)['top'])} top queries")
        return results


def _num(v: Any) -> float:
    """A value from Google as a number; anything that is not a finite, non-negative number counts as 0."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if 0.0 <= x < float("inf") else 0.0


def _index(v: Any) -> float:
    """A point on Google's 0-100 scale. Anything above 100 is not an index value and is held at 100."""
    return min(_num(v), 100.0)


def _utc_label(stamp: int) -> str:
    """A label for a point Google sent without one."""
    if stamp <= 0:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M").removesuffix(" 00:00")


def _fit(values: list[float], n: int) -> list[float]:
    """Exactly ``n`` values: one per term asked for, whatever Google sent."""
    return (values + [0.0] * n)[:n]


def _labels(data: dict, items: list[dict]) -> list[str]:
    """One display name per term: Google's (a topic's name) where it gives one, else the term itself."""
    given = data.get("keywords") or []
    out = []
    for i, item in enumerate(items):
        k = given[i] if i < len(given) and isinstance(given[i], dict) else {}
        out.append(str(k.get("name") or k.get("keyword") or item["keyword"]))
    return out


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _retry_after(resp: Any) -> float:
    try:
        return max(0.0, min(float(resp.headers.get("Retry-After", 0) or 0), 3600.0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _first_sentence(s: str) -> str:
    return s.split(". ")[0].rstrip(".")


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} hours"
    return f"{round(seconds / 86400)} days"
