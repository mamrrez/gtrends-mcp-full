from datetime import timezone

from gtrends_mcp_full.format import (
    explore_url,
    fmt_change,
    fmt_duration,
    fmt_index,
    fmt_points,
    fmt_time,
    fmt_volume,
    md_table,
    safe,
    trending_url,
)


def test_text_from_google_is_made_inert():
    assert safe("a | b [x](y) `z`\nnext") == "a \\| b \\[x\\](y) 'z' next"
    assert safe("<img src=x> </a> <!-- -->") == "&lt;img src=x> &lt;/a> &lt;!-- -->"
    assert safe("<1") == "<1" and safe("a < b") == "a < b"  # a bare less-than is not a tag


def test_index_change_points_and_volume():
    assert [fmt_index(v) for v in (0, 0.4, 1, 49.6, 100)] == ["0", "<1", "1", "50", "100"]
    assert [fmt_change(v) for v in (None, 12.4, -3.6, 0.2, -0.2, 1234.0)] == ["—", "+12%", "-4%", "0%", "0%", "+1,234%"]
    assert [fmt_points(v) for v in (2.44, -1.75, 0.04, -0.04)] == ["+2.4 pts", "-1.8 pts", "0.0 pts", "0.0 pts"]
    assert [fmt_volume(v) for v in (0, 200, 2000, 200000, 2000000, 2500000)] == ["—", "200+", "2K+", "200K+", "2M+", "2.5M+"]


def test_time_and_duration():
    assert fmt_time(0, timezone.utc) == "—" and fmt_time(None, timezone.utc) == "—"
    assert fmt_time(1790820600, timezone.utc) == "2026-10-01 02:10"
    assert [fmt_duration(s) for s in (30, 3000, 4 * 3600, 3 * 86400)] == ["1 min", "50 min", "4 h", "3 d"]


def test_table_caps_rows_and_says_so():
    out = md_table(["a", "b"], [[1, 2.5], ["x|y", None], [3, 4]], max_rows=2)
    assert out.splitlines()[:4] == ["| a | b |", "|---|---|", "| 1 | 2.5 |", "| x\\|y |  |"]
    assert "1 more rows not shown (of 3)" in out


def test_links_to_the_trends_website():
    assert explore_url(["tesla", "byd"], "US", "today 12-m") == "https://trends.google.com/trends/explore?geo=US&q=tesla,byd"
    assert explore_url(["a b"], "", "today 5-y", 47, "youtube") == "https://trends.google.com/trends/explore?date=today%205-y&cat=47&gprop=youtube&q=a%20b"
    assert trending_url("US-CA", 48) == "https://trends.google.com/trending?geo=US-CA&hours=48"
