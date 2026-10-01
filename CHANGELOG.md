# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-10-01

First release: a Google Trends MCP server with 32 tools, 4 prompts and 2 reference
resources, needing no API key, no browser and no sign-in.

### Trending now
- `trending_now` — the full Trending Now list for a country or region, with search volume, growth,
  start time, status, category and the queries inside each trend; filters for category, status,
  volume and text.
- `trend_details` — every query in a trend, the news behind it and its hour-by-hour curve.
- `trending_feed` — the daily trends with headlines and links.
- `trending_across_countries` — trends shared by several countries, grouped across titles.
- `match_trends` — current trends that touch a list of subjects.

### Explore
- `interest_over_time` — up to five terms on one scale, from one hour to 2004-today, with location,
  category, search type (web, YouTube, news, images, shopping) and search operators.
- `interest_by_region` — countries, regions, cities and metro areas; who leads where for several terms.
- `related_queries`, `related_topics` — top and rising, with breakouts flagged.
- `compare_periods`, `compare_locations` — two time ranges, or up to five places, on one scale.
- `keyword_overview` — the whole Trends page for a term in one call.

### Analysis
- `trend_momentum` — breakout, rising, stable, declining or new, with the numbers behind the verdict.
- `seasonality` and `content_calendar` — the yearly rhythm of a term and the date its content should be live.
- `share_of_search` — a brand's share against up to four competitors and how it is moving.
- `compare_many` — 6 to 25 terms on a single scale.
- `find_spikes` — when a term jumped, for how long and by how much.
- `daily_history` — daily data over years, with the weekday pattern.
- `keyword_ideas` — rising and top related queries for several seeds, merged.

### History
- `snapshot_trending`, `trending_history` — Trending Now saved locally and searchable after Google drops it.
- `watchlist_add`, `watchlist_remove`, `watchlist_report` — tracked terms with a record of how their direction changes.
- `history_sql` — read-only SQL over the local data.

### Lookups and utilities
- `find_location`, `find_category`, `find_topic`, `get_capabilities`, `check_endpoints`, `clear_cache`.

### Prompts and resources
- Prompts: `trend_report`, `newsjacking_brief`, `seasonal_content_plan`, `market_comparison`.
- Resources: `gtrends://guide`, `gtrends://trending-categories`.

### Behaviour
- Text matching is by whole words and ignores case.
- Requests are paced; a session cookie is fetched once, allowed to mature, stored, and replaced when
  Google stops honouring it; rate limits back off per group of endpoints; every answer is cached, and
  an older answer is served, labelled, when Google refuses a fresh one — except for the endpoint check
  and for snapshots, which only ever use what Google answers now.
- Each tool call works inside a time budget; a tool that runs out, or is rate-limited part-way, returns
  what it fetched and names what is missing.
- Failures are tool errors with the reason and what to try next.
- Chart requests are paced wider than the rest, and a refusal from Google widens the gap for five minutes.
- Malformed or out-of-range input is refused before Google is asked; malformed answers from Google are
  skipped or reported as such, never a crash.

### Security
- A signed-in cookie or proxy password is removed from every error message; redirects are never followed.
- Text and links from Google are made inert before they reach output: Markdown, control and invisible
  characters, and anything but plain web links.
- `history_sql` is read-only, cannot open another database, is stopped after five seconds, and (Python
  3.11+) cannot build a value over 100 KB. No tool answer exceeds 60,000 characters.
- HTTP mode always checks the Host and Origin headers and refuses to bind to a public address unless
  `GTRENDS_ALLOWED_HOSTS` is set. It has no authentication of its own.
- The data folder and every file in it are created readable by their owner only.
- Command line: `doctor`, `trending`, `snapshot`, `tools`; stdio and streamable HTTP transports.
