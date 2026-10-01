import sqlite3
import time

import pytest

from gtrends_mcp_full.store import Store


def trend(title="x", started=1000, volume=100, **kw):
    return {"title": title, "qkey": title.lower(), "started": started, "ended": None, "volume": volume, "growth": 50, "categories": ["Sports"], "breakdown": [title, title + " live"], **kw}


def test_cache_round_trip_expiry_and_stale(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.cache_put("k", "trending", {"a": ["ب", 1]}, ttl=100)
    value, age, stale = s.cache_get("k")
    assert value == {"a": ["ب", 1]} and age < 5 and stale is False
    s.cache_put("old", "trending", [1], ttl=0.001)
    time.sleep(0.01)
    assert s.cache_get("old") is None
    assert s.cache_get("old", allow_stale=True)[2] is True
    s.cache_put("never", "x", [1], ttl=0)
    assert s.cache_get("never", allow_stale=True) is None
    assert s.cache_get("missing") is None


def test_cache_stats_and_clear(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.cache_put("a", "trending", [1], 100)
    s.cache_put("b", "related", [2], 100)
    s.cache_put("c", "related", [3], 0.001)
    time.sleep(0.01)
    assert s.cache_stats()["entries"] == 3 and s.cache_stats()["fresh"] == 2
    assert s.cache_clear(only_expired=True) == 0  # a recently expired answer is kept as a fallback
    assert s.cache_clear(kind="related") == 2
    assert s.cache_clear() == 1


def test_a_trend_seen_twice_is_updated_not_duplicated(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    assert s.save_trending("US", 24, [trend("a", volume=100), trend("b")], taken_at=5000) == {"geo": "US", "trends": 2, "new": 2, "updated": 0}
    r = s.save_trending("US", 24, [trend("a", volume=900, ended=2000), trend("c")], taken_at=6000)
    assert r["new"] == 1 and r["updated"] == 1
    rows = {t["title"]: t for t in s.trending_search("US", None)}
    assert set(rows) == {"a", "b", "c"}
    assert rows["a"]["volume"] == 900 and rows["a"]["ended"] == 2000 and rows["a"]["first_seen"] == 5000 and rows["a"]["last_seen"] == 6000
    assert rows["a"]["breakdown"] == ["a", "a live"] and rows["a"]["categories"] == ["Sports"]
    s.save_trending("US", 24, [trend("a", volume=10)], taken_at=7000)
    assert {t["title"]: t["volume"] for t in s.trending_search("US", None)}["a"] == 900  # the peak is kept, never lowered


def test_trending_search_filters(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.save_trending("US", 24, [trend("old", started=100), trend("new", started=900, volume=5000)])
    s.save_trending("GB", 24, [trend("brit", started=900)])
    assert [t["title"] for t in s.trending_search("US", since=500)] == ["new"]
    assert {t["title"] for t in s.trending_search(None, None)} == {"old", "new", "brit"}
    assert [t["title"] for t in s.trending_search(None, None, min_volume=1000)] == ["new"]
    cov = {c["geo"]: c for c in s.snapshot_coverage()}
    assert cov["US"]["snapshots"] == 1 and cov["US"]["trends"] == 2 and cov["GB"]["trends"] == 1


def test_watchlist_and_readings(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    assert s.watch_add("tesla", "US", "client a") is True
    assert s.watch_add("tesla", "US") is False
    assert s.watch_add("tesla", "") is True
    assert [(w["keyword"], w["geo"], w["note"]) for w in s.watch_list()] == [("tesla", "", ""), ("tesla", "US", "client a")]
    s.reading_add("tesla", "US", 50, 40, 12.5, "rising", taken_at=100)
    s.reading_add("tesla", "US", 60, 45, None, "stable", taken_at=200)
    assert s.reading_previous("tesla", "US", 200)["label"] == "rising"
    assert s.reading_previous("tesla", "US", 100) is None
    assert s.watch_remove("tesla") == 2 and s.watch_list() == []


def test_sql_is_read_only(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.save_trending("US", 24, [trend("a")])
    cols, rows = s.sql("SELECT title, volume FROM trending")
    assert cols == ["title", "volume"] and rows == [("a", 100)]
    for attempt in (
        "DELETE FROM trending",
        "INSERT INTO watchlist VALUES ('x','',' ',1)",
        "UPDATE trending SET volume = 0 RETURNING title",
        "WITH x AS (SELECT 1) DELETE FROM trending",
        "DROP TABLE trending",
    ):
        # the refusal has to come from SQLite itself, not from a later check that the write already slipped past
        with pytest.raises(sqlite3.Error, match="(?i)readonly|read-only|query_only"):
            s.sql(attempt)
    assert s.sql("SELECT title, volume FROM trending")[1] == [("a", 100)] and s.watch_list() == []


def test_a_runaway_query_is_stopped(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    with pytest.raises(sqlite3.Error, match="ran longer than"):
        s.sql("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c", timeout=0.2)


def test_sql_opens_the_right_file_when_the_path_has_url_characters(tmp_path):
    folder = tmp_path / "data #1 100%"  # "#" and "%" mean something in a URI; both are legal in file names everywhere
    folder.mkdir()
    s = Store(folder / "t.sqlite")
    s.save_trending("US", 24, [trend("a")])
    assert s.sql("SELECT COUNT(*) FROM trending")[1] == [(1,)]


def test_a_later_snapshot_never_reopens_an_ended_trend_or_moves_last_seen_back(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.save_trending("US", 24, [trend("a", ended=2000)], taken_at=6000)
    s.save_trending("US", 24, [trend("a", ended=None)], taken_at=5000)  # an older answer arriving late
    row = s.trending_search("US", None)[0]
    assert row["ended"] == 2000 and row["last_seen"] == 6000
