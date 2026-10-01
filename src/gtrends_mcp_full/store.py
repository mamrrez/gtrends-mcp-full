"""One SQLite file: the response cache, Trending Now snapshots and the watchlist.

Google keeps a trend on the Trending Now page for about a week and then it is
gone. Snapshots saved here are the only way to ask "what was trending in
Germany three weeks ago" later — the history Google does not offer.

The cache is what keeps the server polite: the same question asked twice
costs one request, and when Google is rate-limiting, an expired answer can
still be served (and labelled as such) instead of an error.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  key      TEXT PRIMARY KEY,
  kind     TEXT NOT NULL,
  created  REAL NOT NULL,
  expires  REAL NOT NULL,
  body     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id        INTEGER PRIMARY KEY,
  taken_at  INTEGER NOT NULL,            -- unix seconds, UTC
  geo       TEXT NOT NULL,
  hours     INTEGER NOT NULL,
  trends    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trending (
  geo        TEXT NOT NULL,
  title      TEXT NOT NULL,
  qkey       TEXT NOT NULL,              -- the title lower-cased, without punctuation
  started    INTEGER NOT NULL,           -- unix seconds, UTC
  ended      INTEGER,                    -- NULL while the trend is still active
  volume     INTEGER NOT NULL,           -- highest search volume seen
  growth     INTEGER NOT NULL,           -- highest growth percent seen
  categories TEXT NOT NULL,              -- JSON list of category names
  breakdown  TEXT NOT NULL,              -- JSON list of the queries inside the trend
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  PRIMARY KEY (geo, title, started)
);
CREATE INDEX IF NOT EXISTS trending_qkey ON trending (qkey);
CREATE INDEX IF NOT EXISTS trending_started ON trending (geo, started);
CREATE TABLE IF NOT EXISTS watchlist (
  keyword   TEXT NOT NULL,
  geo       TEXT NOT NULL,
  note      TEXT NOT NULL DEFAULT '',
  added_at  INTEGER NOT NULL,
  PRIMARY KEY (keyword, geo)
);
CREATE TABLE IF NOT EXISTS readings (
  keyword   TEXT NOT NULL,
  geo       TEXT NOT NULL,
  taken_at  INTEGER NOT NULL,
  latest    REAL NOT NULL,               -- last complete value on that pull's own 0-100 scale
  average   REAL NOT NULL,
  yoy       REAL,                        -- last 3 months vs the same months a year before, in percent
  label     TEXT NOT NULL,
  PRIMARY KEY (keyword, geo, taken_at)
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- connection ---------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # The file is created owner-only before SQLite opens it: SQLite gives its -wal and -shm
                # companions the mode of the main file, and they hold the same data.
                with contextlib.suppress(OSError):
                    os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
                    for name in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
                        if name.exists():
                            os.chmod(name, 0o600)
                conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15.0)
                conn.row_factory = sqlite3.Row
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                conn.commit()
                with contextlib.suppress(OSError):
                    os.chmod(self.path, 0o600)
                self._conn = conn
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- cache --------------------------------------------------------------

    def cache_get(self, key: str, allow_stale: bool = False) -> tuple[object, float, bool] | None:
        """``(value, age_seconds, is_stale)`` or ``None``."""
        with self._lock:
            row = self._db().execute("SELECT created, expires, body FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        now = time.time()
        stale = now >= row["expires"]
        if stale and not allow_stale:
            return None
        try:
            return json.loads(row["body"]), now - row["created"], stale
        except ValueError:
            return None

    def cache_put(self, key: str, kind: str, value: object, ttl: float) -> None:
        if ttl <= 0:
            return
        now = time.time()
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            db = self._db()
            db.execute("INSERT OR REPLACE INTO cache (key, kind, created, expires, body) VALUES (?,?,?,?,?)", (key, kind, now, now + ttl, body))
            db.commit()

    def cache_stats(self) -> dict:
        now = time.time()
        with self._lock:
            row = self._db().execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(body)),0) AS bytes, COALESCE(SUM(expires > ?),0) AS fresh FROM cache", (now,)
            ).fetchone()
        return {"entries": row["n"], "fresh": row["fresh"], "bytes": row["bytes"]}

    def cache_clear(self, kind: str | None = None, only_expired: bool = False) -> int:
        sql, args = "DELETE FROM cache", []
        where = []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if only_expired:
            # keep a week of expired answers: they are the fallback while Google rate-limits
            where.append("expires < ?")
            args.append(time.time() - 7 * 86400)
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            db = self._db()
            n = db.execute(sql, args).rowcount
            db.commit()
        return n

    # -- trending snapshots ---------------------------------------------------

    def save_trending(self, geo: str, hours: int, trends: Iterable[dict], taken_at: int | None = None) -> dict:
        """Store one Trending Now pull. A trend seen again is updated, never duplicated."""
        taken_at = int(taken_at or time.time())
        new = updated = total = 0
        with self._lock:
            db = self._db()
            for t in trends:
                total += 1
                key = (geo, t["title"], int(t["started"]))
                row = db.execute("SELECT volume, growth FROM trending WHERE geo=? AND title=? AND started=?", key).fetchone()
                cats = json.dumps(t.get("categories", []), ensure_ascii=False)
                breakdown = json.dumps(t.get("breakdown", []), ensure_ascii=False)
                if row is None:
                    new += 1
                    db.execute(
                        "INSERT INTO trending (geo,title,qkey,started,ended,volume,growth,categories,breakdown,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            geo, t["title"], t.get("qkey") or t["title"].lower(), int(t["started"]), t.get("ended"),
                            int(t.get("volume") or 0), int(t.get("growth") or 0), cats, breakdown, taken_at, taken_at,
                        ),
                    )  # fmt: skip
                else:
                    updated += 1
                    db.execute(
                        "UPDATE trending SET ended=COALESCE(?, ended), volume=MAX(volume,?), growth=MAX(growth,?), categories=?, breakdown=?, last_seen=MAX(last_seen, ?) WHERE geo=? AND title=? AND started=?",
                        (t.get("ended"), int(t.get("volume") or 0), int(t.get("growth") or 0), cats, breakdown, taken_at, *key),
                    )
            db.execute("INSERT INTO snapshots (taken_at, geo, hours, trends) VALUES (?,?,?,?)", (taken_at, geo, int(hours), total))
            db.commit()
        return {"geo": geo, "trends": total, "new": new, "updated": updated}

    def trending_search(self, geo: str | None, since: int | None, until: int | None = None, min_volume: int = 0, limit: int | None = None) -> list[dict]:
        """Saved trends, newest first. Text matching is left to the caller."""
        sql = "SELECT * FROM trending WHERE volume >= ?"
        args: list = [int(min_volume)]
        if geo:
            sql += " AND geo = ?"
            args.append(geo)
        if since is not None:
            sql += " AND started >= ?"
            args.append(int(since))
        if until is not None:
            sql += " AND started <= ?"
            args.append(int(until))
        sql += " ORDER BY started DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._lock:
            rows = self._db().execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["categories"] = json.loads(d["categories"])
            d["breakdown"] = json.loads(d["breakdown"])
            out.append(d)
        return out

    def snapshot_coverage(self) -> list[dict]:
        with self._lock:
            rows = self._db().execute(
                """SELECT s.geo AS geo, COUNT(*) AS snapshots, MIN(s.taken_at) AS first, MAX(s.taken_at) AS last,
                          (SELECT COUNT(*) FROM trending t WHERE t.geo = s.geo) AS trends
                   FROM snapshots s GROUP BY s.geo ORDER BY s.geo"""
            ).fetchall()
        return [dict(r) for r in rows]

    # -- watchlist ------------------------------------------------------------

    def watch_add(self, keyword: str, geo: str, note: str = "") -> bool:
        with self._lock:
            db = self._db()
            cur = db.execute("INSERT OR IGNORE INTO watchlist (keyword, geo, note, added_at) VALUES (?,?,?,?)", (keyword, geo, note, int(time.time())))
            if cur.rowcount == 0 and note:
                db.execute("UPDATE watchlist SET note=? WHERE keyword=? AND geo=?", (note, keyword, geo))
            db.commit()
            return cur.rowcount > 0

    def watch_remove(self, keyword: str, geo: str | None = None) -> int:
        with self._lock:
            db = self._db()
            if geo is None:
                n = db.execute("DELETE FROM watchlist WHERE keyword = ?", (keyword,)).rowcount
            else:
                n = db.execute("DELETE FROM watchlist WHERE keyword = ? AND geo = ?", (keyword, geo)).rowcount
            db.commit()
        return n

    def watch_list(self) -> list[dict]:
        with self._lock:
            rows = self._db().execute("SELECT keyword, geo, note, added_at FROM watchlist ORDER BY geo, keyword").fetchall()
        return [dict(r) for r in rows]

    def reading_add(self, keyword: str, geo: str, latest: float, average: float, yoy: float | None, label: str, taken_at: int | None = None) -> None:
        with self._lock:
            db = self._db()
            db.execute(
                "INSERT OR REPLACE INTO readings (keyword, geo, taken_at, latest, average, yoy, label) VALUES (?,?,?,?,?,?,?)",
                (keyword, geo, int(taken_at or time.time()), float(latest), float(average), yoy, label),
            )
            db.commit()

    def reading_previous(self, keyword: str, geo: str, before: int) -> dict | None:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM readings WHERE keyword=? AND geo=? AND taken_at < ? ORDER BY taken_at DESC LIMIT 1", (keyword, geo, int(before))
            ).fetchone()
        return dict(row) if row else None

    # -- read-only SQL --------------------------------------------------------

    def sql(self, query: str, limit: int = 200, timeout: float = 5.0) -> tuple[list[str], list[tuple]]:
        """Run one SELECT on a read-only connection, for at most ``timeout`` seconds."""
        self._db()  # make sure the file and schema exist
        # as_uri() escapes "#", "?" and "%" in the path, which would otherwise cut the file name short
        conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            if hasattr(conn, "setlimit"):  # Python 3.11+
                # No single value larger than 100 KB: without this, one zeroblob() or a doubling string
                # costs gigabytes of memory inside the time limit. And no other database file, ever.
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)
                conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            deadline = time.monotonic() + timeout
            conn.set_progress_handler(lambda: time.monotonic() > deadline, 20_000)  # a runaway query is interrupted
            try:
                cur = conn.execute(query)
                if cur.description is None:
                    raise sqlite3.OperationalError("only statements that return rows are allowed")
                cols = [c[0] for c in cur.description]
                return cols, [tuple(r) for r in cur.fetchmany(int(limit))]
            except sqlite3.Warning as e:  # Python 3.10 reports "one statement at a time" as a Warning, not an Error
                raise sqlite3.ProgrammingError(str(e)) from None
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e).lower():
                    raise sqlite3.OperationalError(f"the query ran longer than {timeout:g} seconds and was stopped") from e
                raise
        finally:
            conn.close()
