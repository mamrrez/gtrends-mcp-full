---
title: Tool reference
nav_order: 4
description: Every Google Trends tool with its parameters, generated from the server itself.
---

# Tool reference
{: .no_toc }

32 tools in gtrends-mcp-full 0.1.2. This page is generated from the code by `scripts/gen_tools_doc.py`.

Tools marked **local** never contact Google: they work on the data file on your machine.

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Trending now

### `trending_now`

What people are searching right now: the full Trending Now list for a country or region.

        Each trend has its search volume, growth, start time, whether it is still active, its
        category and the other queries people type for the same story.

        geo: country or region code (US, GB, DE, IR, US-CA). Blank = the default location, else US.
        hours: look-back window — 4, 24, 48 or 168 (any value 1-191).
        category: filter by name, e.g. "sports", "technology", "business" (see the header of the result).
        status: "active" (still trending), "ended" or "all".
        sort_by: "volume", "growth" or "recent".
        contains: keep trends whose title or queries contain this text, as whole words.

| Parameter | Type | Default |
|---|---|---|
| `geo` | string | `""` |
| `hours` | integer | `24` |
| `category` | string | `""` |
| `status` | string | `"all"` |
| `sort_by` | string | `"volume"` |
| `min_volume` | integer | `0` |
| `contains` | string | `""` |
| `limit` | integer | `25` |

### `trend_details`

Everything about one current trend: every query in it, the news behind it, and its hour-by-hour curve.

        trend: the trend's title, or any words it contains.
        geo: country or region code; blank = the default location, else US.
        with_chart: also fetch the last 7 days of hourly interest for the title (two more requests).

| Parameter | Type | Default |
|---|---|---|
| `trend` | string | **required** |
| `geo` | string | `""` |
| `hours` | integer | `48` |
| `news` | integer | `5` |
| `with_chart` | boolean | `true` |

### `trending_feed`

The top daily search trends with the news stories behind each — Google's public trending feed.

        Lighter than trending_now (about 10-20 trends, one request) and it carries headlines and
        links, which makes it the quickest answer to "what is in the news in <country> today".
        geo: country code; blank = the default location, else US.

| Parameter | Type | Default |
|---|---|---|
| `geo` | string | `""` |
| `limit` | integer | `10` |

### `trending_across_countries`

Trends that are live in several countries at once — the stories that travel.

        Pulls Trending Now for each country and groups trends that are the same search (the same
        title, or one country's title appearing among another's queries).

        geos: comma-separated country codes, up to 12. Blank = US, GB, CA, AU, IN, DE, FR, BR.
        min_countries: only show trends found in at least this many of them.

| Parameter | Type | Default |
|---|---|---|
| `geos` | string | `""` |
| `hours` | integer | `24` |
| `min_countries` | integer | `2` |
| `limit` | integer | `30` |

### `match_trends`

Which current trends touch your subjects — for newsjacking and timely content.

        terms: comma-separated words or phrases that define your niche (brands, products, people,
        subjects). A trend matches when its title or any query inside it contains one of them as
        whole words, in any letter case: "ai" finds «ai news», not «rain».
        geo: country or region code; blank = the default location, else US.

| Parameter | Type | Default |
|---|---|---|
| `terms` | string | **required** |
| `geo` | string | `""` |
| `hours` | integer | `48` |
| `limit` | integer | `25` |

---

## Explore

### `keyword_overview`

The whole Google Trends page for one term in a single call: curve, direction, top places, related searches.

        Use it as the first look at a keyword. It makes four requests; a part that Google refuses
        is reported and the rest is still returned.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |

### `interest_over_time`

Search interest over time for up to 5 terms on one shared scale — the main Google Trends chart.

        keywords: comma-separated terms or topic ids (find_topic). Operators work inside a term:
        `a + b` either wording, `a -b` excluding b, `"a b"` exact phrase. Blank with a `category`
        measures the whole category.
        timeframe: 1h, 4h, 1d, 7d, 1m, 3m, 12m, 5y, all; any 6m / 2y / 45d; a year 2024; a month
        2024-03; or explicit dates "2024-01-01 2024-12-31". Short ranges come back by the minute
        or hour, up to ~9 months daily, up to 5 years weekly, longer monthly.
        geo: location code (find_location); blank = default location, usually worldwide.
        category: category id (find_category) to pin down an ambiguous term.
        property: web, youtube, news, images or shopping.
        points: rows of data to return (longer series are averaged down); 0 = summary only.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | `""` |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `points` | integer | `26` |

### `interest_by_region`

Where a term is most popular: countries, regions, cities or metro areas ranked by interest.

        With one term, each place gets 0-100 relative to the strongest place — interest as a share
        of that place's own searches, so a small region can outrank a large one. With 2-5 terms,
        each place shows how its interest splits between them (percentages), i.e. who wins where.

        geo: blank = worldwide (ranks countries); a country ranks its regions; a region its cities.
        resolution: auto, country, region, city, or dma (US metro areas).
        include_low_volume: also list places with little search volume.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `resolution` | string | `"auto"` |
| `include_low_volume` | boolean | `false` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `limit` | integer | `25` |

### `related_queries`

What else people who search a term also search: the top related queries and the rising ones.

        Rising = the largest growth against the previous period; "Breakout" means more than +5000%,
        usually a query that barely existed before. Top = the most searched, 100 being the most common.
        keyword: one term or topic id. timeframe, geo, category, property: as in interest_over_time.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `limit` | integer | `25` |

### `related_topics`

The topics (people, brands, things) searched alongside a term, top and rising, with their topic ids.

        Google withholds this list from anonymous sessions: without a signed-in cookie
        (GTRENDS_COOKIE or GTRENDS_COOKIES_FILE) it usually comes back empty, and this tool says so
        rather than pretending there are none. related_queries works either way.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `limit` | integer | `25` |

### `compare_periods`

One term in two time ranges on the same scale: this year against last year, this quarter against the one before.

        period: the range to look at (3m, 12m, 2026, 2026-03, or explicit dates; not hours).
        against: "year_before" (same dates a year earlier), "previous" (the range just before), or another range.
        The two curves are aligned by position, so day 10 of one sits beside day 10 of the other.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `period` | string | `"12m"` |
| `against` | string | `"year_before"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `points` | integer | `13` |

### `compare_locations`

One term in up to 5 countries or regions side by side: where it matters more, and whether the curves move together.

        geos: comma-separated location codes (US, GB, DE) — countries or regions, not worldwide.
        The values say how large a share of each place's own searches the term takes, so a small
        country can score above a large one; they are not search counts.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `geos` | string | **required** |
| `timeframe` | string | `"12m"` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `points` | integer | `13` |

---

## Analysis

### `trend_momentum`

Is each term growing, fading, flat or exploding — with the numbers behind the verdict.

        Each term (up to 8) is measured on its own scale, so a small term's shape is not flattened
        by a large one. Per term: year-over-year change of the last quarter, last quarter against
        the quarter before, the long-run slope, where it stands against its own peak, and a label:
        breakout, rising, stable, declining or new.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | **required** |
| `geo` | string | `""` |
| `timeframe` | string | `"5y"` |
| `category` | integer | `0` |
| `property` | string | `"web"` |

### `seasonality`

The yearly rhythm of a term: which months it peaks and dips, how reliably, and when to publish for it.

        Uses five years of data by default ("all" goes back to 2004). Growth or decline is removed
        first, so a rising term is not mistaken for a seasonal one. Returns a month-by-month index
        (100 = an average month), the peak and trough, how consistent the pattern is from year to
        year, year averages, the months ahead on the current rhythm, and a publishing date.

        lead_weeks: how long before demand starts to climb the content should be live.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `geo` | string | `""` |
| `timeframe` | string | `"5y"` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `lead_weeks` | integer | `8` |

### `content_calendar`

A publishing calendar from seasonality: for up to 8 topics, when each peaks and the date its content must be live.

        Each term's five-year rhythm is measured; the result is sorted by what needs publishing
        soonest. Terms without a yearly pattern are listed as evergreen.
        lead_weeks: how long before demand starts to climb the content should be published.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | **required** |
| `geo` | string | `""` |
| `lead_weeks` | integer | `8` |
| `category` | integer | `0` |
| `property` | string | `"web"` |

### `share_of_search`

Share of search: your brand's portion of all searches for you and up to 4 competitors, and how it is moving.

        Share of search tracks market share closely in many categories and moves before it, which
        makes it a cheap leading indicator. Returns each brand's share over the whole range, at
        its start and at its end (first and last fifth of the range), and the change in points.
        Use topic ids (find_topic) when a brand name has other meanings.

| Parameter | Type | Default |
|---|---|---|
| `brand` | string | **required** |
| `competitors` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |

### `compare_many`

Rank 6 to 25 terms on one scale — past Google Trends' limit of five per comparison.

        Google rescales every comparison to its own maximum, so two separate five-term charts
        cannot be read against each other. This tool runs the terms in groups that all contain one
        shared *anchor* term and uses it to bring every group onto a single scale.

        anchor: the shared term. Blank = chosen automatically (a mid-sized term among the first five,
        which keeps both large and small terms measurable).
        Returns each term's average, peak and latest value, where the largest average is 100.
        Costs two requests per group of four terms.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `anchor` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |

### `find_spikes`

The moments a term suddenly jumped: when each spike started, when it peaked, how long it lasted and how big it was.

        A spike is a run of points at least `threshold` times the term's usual level around that
        date (its median over the surrounding year). Use it to date events, launches, outages and
        news cycles, or to tell a one-off burst from real growth.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `timeframe` | string | `"5y"` |
| `geo` | string | `""` |
| `threshold` | number | `2.0` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `limit` | integer | `15` |

### `daily_history`

Day-by-day interest over a long range — Google Trends itself only gives daily data for up to ~9 months.

        The range is fetched as overlapping 8-month windows and the windows are joined on one
        0-100 scale using the days they share. Up to about 4 years (8 windows, two requests each).
        Also returns the weekday pattern: which days of the week the term is searched most.

        start, end: YYYY-MM-DD; end defaults to today.
        points: rows to return (the daily series is averaged down to this many); the summary is always given.

| Parameter | Type | Default |
|---|---|---|
| `keyword` | string | **required** |
| `start` | string | **required** |
| `end` | string | `""` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `points` | integer | `30` |

### `keyword_ideas`

Keyword ideas from what people actually search next: rising and top related queries for up to 5 seed terms, merged.

        The same query coming from several seeds is listed once; each idea shows which seeds it came from —
        an idea related to several seeds sits at the centre of the subject. Rising ideas are sorted
        breakouts first: these are the searches that did not exist a period ago.
        Costs two paced requests per seed.

| Parameter | Type | Default |
|---|---|---|
| `seeds` | string | **required** |
| `timeframe` | string | `"12m"` |
| `geo` | string | `""` |
| `category` | integer | `0` |
| `property` | string | `"web"` |
| `limit` | integer | `40` |

---

## History

### `snapshot_trending`

Save the current Trending Now list to the local history, so it can still be searched after Google drops it.

        Google shows a trend for about a week; after that there is no way to ask what was trending.
        Each call stores every trend for the given countries (a trend seen again is updated, not
        duplicated). Run it on a schedule — `gtrends-mcp-full snapshot US,GB` from cron does the
        same — and use trending_history to query the result.

        geos: comma-separated country or region codes (up to 12); blank = the default location, else US.
        hours: look-back window to save, 1-191. 24 is right for a daily schedule.

| Parameter | Type | Default |
|---|---|---|
| `geos` | string | `""` |
| `hours` | integer | `24` |

### `trending_history` — local

Search the saved Trending Now history: when something trended, how big it got and how long it lasted.

        Works on what snapshot_trending has stored — no network. With no filter it lists the
        biggest saved trends of the period.

        contains: text the trend's title or queries must contain, as whole words.
        geo: one location code; blank = every saved location.
        days: how far back to look, counted from the trend's start.

| Parameter | Type | Default |
|---|---|---|
| `contains` | string | `""` |
| `geo` | string | `""` |
| `days` | integer | `30` |
| `min_volume` | integer | `0` |
| `limit` | integer | `50` |

### `watchlist_add` — local

Put terms on the watchlist so watchlist_report tracks their direction over time.

        keywords: comma-separated terms or topic ids. geo: the location to track them in (blank = default).
        note: an optional label, e.g. the client or campaign they belong to.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | **required** |
| `geo` | string | `""` |
| `note` | string | `""` |

### `watchlist_remove` — local

Take terms off the watchlist. Blank geo removes them from every location; their past readings are kept.

| Parameter | Type | Default |
|---|---|---|
| `keywords` | string | **required** |
| `geo` | string | `""` |

### `watchlist_report`

The watchlist measured now: each term's direction, what changed since the last report, and whether it is trending today.

        Every run stores a reading per term, so the next report can say "was stable, now rising".
        Terms are measured over five years, one at a time (two requests each): `limit` terms per
        call, `offset` to continue. Also checks today's Trending Now list for each term.

| Parameter | Type | Default |
|---|---|---|
| `geo` | string | `""` |
| `limit` | integer | `10` |
| `offset` | integer | `0` |

### `history_sql` — local

Run a read-only SQL SELECT on the local data file, for questions the other history tools do not cover.

        Tables:
        trending(geo, title, qkey, started, ended, volume, growth, categories, breakdown, first_seen, last_seen)
          — one row per saved trend; times are unix seconds UTC; categories and breakdown are JSON lists.
        snapshots(id, taken_at, geo, hours, trends) — one row per snapshot_trending run.
        watchlist(keyword, geo, note, added_at)
        readings(keyword, geo, taken_at, latest, average, yoy, label) — one row per watchlist_report measurement.
        Example: SELECT title, volume, datetime(started,'unixepoch') FROM trending WHERE geo='US' ORDER BY volume DESC LIMIT 20

| Parameter | Type | Default |
|---|---|---|
| `query` | string | **required** |
| `limit` | integer | `100` |

---

## Lookups and utilities

### `find_location`

Find the location code (geo) for a country, region, state or metro area by name.

        query: part of a name ("tehran", "calif", "bavaria") or a code ("IR").
        inside: a code whose sub-locations to list instead ("IR" → its provinces, "US-CA" → its metro areas).
        Codes look like IR, US, US-CA, US-CA-807. Every other tool takes them as `geo`; blank means worldwide.

| Parameter | Type | Default |
|---|---|---|
| `query` | string | `""` |
| `inside` | string | `""` |
| `limit` | integer | `25` |

### `find_category`

Find the id of a Google Trends category ("Autos & Vehicles" = 47) to narrow a term to one meaning.

        A category limits a search term to searches Google files under that subject — "jaguar" in
        Autos & Vehicles is the car, in Pets & Animals the cat. Pass the id as `category` to the explore tools.
        With no keyword at all, a category id shows interest in the whole subject.

| Parameter | Type | Default |
|---|---|---|
| `query` | string | **required** |
| `limit` | integer | `25` |

### `find_topic`

Find the Google Trends *topics* for a piece of text and the ids that select them.

        A search term ("tesla") counts only searches containing that exact wording, in one language.
        A topic ("Tesla — Automotive company", id /m/0dr90d) counts every search about the thing,
        in any language and spelling, and leaves out other meanings (the band, the inventor).
        Pass a topic id wherever a keyword is expected.

| Parameter | Type | Default |
|---|---|---|
| `text` | string | **required** |

### `get_capabilities` — local

Version, settings, session and cache state, saved history and the list of tools. Call this first when unsure.

### `check_endpoints`

Ask each Google Trends endpoint one small question and report which ones answer.

        Google Trends has no public API; this server uses the endpoints of the Trends website,
        which can change without notice. Run this when a tool fails unexpectedly: it separates
        "Google is rate-limiting me" from "this endpoint changed" from "the network is down".
        Sends about 12 requests and never answers from the cache.

### `clear_cache` — local

Delete saved answers so the next call asks Google again.

        what: "expired" (default — only answers too old to be used even as a fallback), "all", or one kind:
        trending, timeseries, regions, related, explore.
        reset_session: also forget the stored session cookie and any back-off, for a clean start after a block.
        Saved Trending Now history and the watchlist are never touched.

| Parameter | Type | Default |
|---|---|---|
| `what` | string | `"expired"` |
| `reset_session` | boolean | `false` |

