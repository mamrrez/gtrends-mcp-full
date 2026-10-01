---
title: Using it
nav_order: 3
description: How to ask, how to read the 0-100 index, time ranges, operators, topics, categories and locations.
---

# Using it
{: .no_toc }

1. TOC
{:toc}

## Reading the numbers

Everything Google Trends returns — apart from Trending Now's search volumes — is an **index from 0 to 100**:

1. Google takes the share of *all* searches that a term had at each moment (or in each place).
2. It rescales the whole answer so the highest point in it is 100.

So a value is never a count of searches. What follows from that:

| | |
|---|---|
| **Only terms in one request are comparable.** | Two separate calls are on two separate scales. `compare_many`, `share_of_search`, `compare_periods` and `compare_locations` build a single scale for you. |
| **Across places it is a share of local searches.** | A small country can outrank a large one. The place with 100 is where the term matters most, not where most people searched. |
| **`<1` and `0` are different.** | `<1` is a trace of interest; `0` is too little to measure. |
| **The last point is usually partial.** | It is still being collected. Tables mark it with `*` and averages leave it out. |
| **It is a sample.** | The same request on another day can differ by a point or two. |

## Time ranges

| You write | You get |
|---|---|
| `1h` `4h` | one point per minute |
| `1d` | one point per 8 minutes |
| `7d` | hourly |
| `1m` `3m`, or any range under about 9 months | daily |
| `12m` `5y`, or up to 5 years | weekly |
| `all`, or longer than 5 years | monthly, back to 2004 |

Also accepted: `6m`, `2y`, `45d` · a year `2024` · a month `2024-03` · years `2019-2023` · explicit dates `2024-01-01 2024-12-31` · an hourly range `2026-09-28T00 2026-09-30T00` (at most 7 days).

For daily data over a longer range, use `daily_history`: it joins overlapping windows onto one scale.

## Terms, operators and topics

A **search term** matches searches containing those words, in that language and spelling.

| Write | Meaning |
|---|---|
| `tesla model y` | searches containing all three words, in any order |
| `"tesla model y"` | that exact phrase |
| `tesla + byd` | either |
| `jaguar -car` | jaguar without "car" |

A **topic** is Google's own grouping of every search about one thing, in any language. `find_topic` returns topic ids such as `/m/0dr90d` (Tesla, the company); pass one wherever a keyword goes. Do not compare a topic with a search term — they are measured differently.

A **category** narrows an ambiguous word: "jaguar" in *Autos & Vehicles* is the car. `find_category` gives the ids. With no keyword at all, a category id measures the whole category.

## Locations

Locations are codes: `US`, `IR`, `DE`; regions `US-CA`, `IR-07`; US metro areas `US-CA-807`. `find_location` finds them by name or lists what is inside one. Leave `geo` blank for worldwide — except in the Trending Now tools, which need a country.

Set `GTRENDS_GEO` to make one location the default.

## Search types

`property` is one of `web` (default), `youtube`, `news`, `images`, `shopping`.

## Good habits

- **Several keywords in one call.** `interest_over_time` takes five; `trend_momentum` and `content_calendar` eight; `compare_many` twenty-five. One call each is slower and more likely to hit a rate limit.
- **Start with `keyword_overview`.** It is the whole Trends page in four requests.
- **A term is matched literally.** If people write it in more than one way, join the wordings with `+` in one term, or use a topic id.
- **Use a long range for verdicts.** Twelve months cannot tell a season from a trend; `trend_momentum` and `seasonality` use five years for that reason.

## Prompts

Four workflows appear in your client's prompt menu:

| Prompt | Arguments | Result |
|---|---|---|
| `trend_report` | keyword, geo | Direction, seasonality, the events behind the spikes, rising themes, recommendations |
| `newsjacking_brief` | subjects, geo | Today's trends that fit your subjects, with the story, the queries, an angle and a risk note |
| `seasonal_content_plan` | topics, geo, lead_weeks | A twelve-month publishing plan |
| `market_comparison` | brand, competitors, geo | Share of search, who wins where, what is rising around each brand |
