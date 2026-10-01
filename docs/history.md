---
title: History
nav_order: 5
description: Keep Trending Now past Google's one week, track a watchlist, and query both with SQL.
---

# History

Everything in this section lives in one SQLite file on your machine (`~/.config/gtrends-mcp-full/trends.sqlite` unless `GTRENDS_DB_PATH` says otherwise).

## Trending Now, kept

Google lists a trend for about a week. After that there is no way to ask what was trending.

`snapshot_trending` saves the current list — every trend with its volume, growth, times, categories and queries. A trend seen again is updated (its peak volume and end time), never duplicated. Run it by hand, or on a schedule:

```cron
0 */6 * * * gtrends-mcp-full snapshot US,GB,DE
```

Every six hours with the default 24-hour window catches each trend several times, which is what records how it grew and when it ended.

`trending_history` then answers, without contacting Google:

- *"Did anything about 'iphone' trend in the US last month?"*
- *"What were the biggest trends in Germany in September?"*
- *"How long did the 'budget' trend last?"*

Text is matched by whole words, ignoring case, as everywhere else: "ai" finds «ai news», not «rain».

## The watchlist

`watchlist_add` stores terms per location with an optional note. `watchlist_report` measures each over five years and reports:

- its direction — breakout, rising, stable, declining, new;
- year-over-year and quarter-over-quarter change;
- **what changed since the last report** ("was stable on 2026-09-01");
- whether the term is in today's Trending Now list.

Each run stores a reading, so the reports build a record of their own. Terms are measured one at a time, two requests each: ten per call by default, with `offset` to continue.

## SQL

`history_sql` runs one read-only `SELECT`:

| Table | Columns |
|---|---|
| `trending` | `geo, title, qkey, started, ended, volume, growth, categories, breakdown, first_seen, last_seen` |
| `snapshots` | `id, taken_at, geo, hours, trends` |
| `watchlist` | `keyword, geo, note, added_at` |
| `readings` | `keyword, geo, taken_at, latest, average, yoy, label` |

Times are unix seconds in UTC; `categories` and `breakdown` are JSON lists.

```sql
-- the categories that trended most in Germany this month
SELECT j.value AS category, COUNT(*) AS trends, SUM(volume) AS searches
FROM trending, json_each(trending.categories) AS j
WHERE geo = 'DE' AND started >= strftime('%s', 'now', '-30 days')
GROUP BY category ORDER BY searches DESC;
```

## The cache

The same file holds cached answers. `clear_cache` removes them (`expired`, `all`, or one kind); it never touches the trending history or the watchlist. Expired answers are kept for a week as a fallback for when Google refuses a fresh one.
