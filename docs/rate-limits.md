---
title: Rate limits
nav_order: 6
description: How the server avoids being blocked by Google Trends, and what to do when it is.
---

# Rate limits
{: .no_toc }

1. TOC
{:toc}

Google Trends has no public API for general use. This server uses the endpoints behind the Trends website. They are rate-limited per connection, and the limits are not published. This page says what the server does about that and what you can do.

## What the server does

### One long-lived session

The chart endpoints refuse a request that carries no Google session cookie, and hand one out with the refusal. But Google does not trust a cookie it has only just issued: on a network it is wary of, requests carrying a brand-new cookie are turned away for about a minute and a half.

So the server:

- fetches a cookie in the background as soon as it starts, so it has matured before the first chart is asked for;
- stores it and reuses it on every later run — a cookie Google already knows;
- never sends a new, untrusted cookie to the endpoints that work without one (Trending Now, the feed, the lookups), so those keep working from the first second;
- keeps one cookie instead of collecting a new one with every refusal, which is what gets a connection flagged;
- replaces it only when Google itself refuses the established one and issues another, or when it is older than five months.

The only time you will notice any of this is the very first start, when a chart tool may answer *"Google is still validating this server's new session … about 90 more seconds"*.

### Pacing

Requests are at least `GTRENDS_MIN_INTERVAL` seconds apart (1.5 by default); chart requests get half a second more. When Google refuses a request, the gap for that group of endpoints widens by half — up to three times the usual — and returns to normal after five quiet minutes. The related-searches endpoint has the smallest quota: two requests to it are kept 6 seconds apart, whatever goes in between.

### Back-off, then silence

A real rate limit is retried with growing delays (about 4, 9 and 18 seconds). If it persists, the server stops asking **that group of endpoints** for a few minutes — continuing would only lengthen the block. (A single refusal with no time left in the call to retry earns a pause of 15 seconds.) The groups are independent:

| Group | Tools that use it |
|---|---|
| charts | interest over time, regions, related searches, and every analysis tool |
| trending | Trending Now, trend news, the feed |
| lookups | topics, locations, categories |

### The cache and its fallback

Every answer is stored: charts for an hour, Trending Now for five minutes, locations and categories for a month. When Google refuses a fresh answer and an older one exists, the older one is returned with a note saying how old it is.

Two things never come from the cache: `check_endpoints` / `doctor`, which exist to tell you what Google answers right now, and `snapshot_trending`, which would otherwise record an old list as a new observation.

### Fewer requests by design

- One chart request carries up to five terms.
- A second chart for the same question (regions after the time series, say) reuses the first one's signed tokens.
- Tools that need many requests stop cleanly when a limit hits or the call's time budget runs out: they return what they have and name what is missing.

## What you can do

**Ask for several keywords in one call.** This matters more than anything else.

**Wait.** A block usually clears in a few minutes. Cached answers keep working.

**Slow down.** `GTRENDS_MIN_INTERVAL=3` halves the request rate.

**Use a proxy.** `GTRENDS_PROXY=http://user:pass@host:port` sends requests through another connection.

**Start clean.** `clear_cache` with `reset_session=true` drops the stored cookie and the back-off state. The next chart request will wait for a new cookie to mature.

## Checking the state

`get_capabilities` shows requests sent, cache hits and whether the server is backing off. `check_endpoints` (or `gtrends-mcp-full doctor`) asks each endpoint one question and tells a rate limit (HTTP 429) from a changed endpoint (HTTP 404 or an unreadable answer) from a network failure.
