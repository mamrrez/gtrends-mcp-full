---
title: Troubleshooting
nav_order: 7
description: The errors people actually hit, and the fix for each.
---

# Troubleshooting
{: .no_toc }

Run `gtrends-mcp-full doctor` first — it prints the configuration it sees and checks every endpoint. Most problems are visible there.

1. TOC
{:toc}

## "Google is still validating this server's new session"

The first minute and a half after the very first start (or after `clear_cache` with `reset_session`). Google does not accept a session cookie it has just issued. Trending tools work in the meantime; try the chart tool again shortly. It does not recur: the session is stored. See [Rate limits](rate-limits).

## "Google Trends is rate-limiting this connection (HTTP 429)"

Too many requests from this connection. Wait a few minutes; the server has stopped asking that group of endpoints so the block can clear. Cached answers still work. If it happens often: several keywords per call, a larger `GTRENDS_MIN_INTERVAL`, or `GTRENDS_PROXY`.

## "Google Trends rejected the request (HTTP 400)"

Google's answer to a location code, category id or time range it does not know. Check the code with `find_location` / `find_category`. More than five terms is caught before a request is sent.

## "Google Trends has no such endpoint any more (HTTP 404)"

An endpoint behind the Trends website changed. Run `check_endpoints` to see which, and update the package.

## "too little search volume"

Google returns nothing below a volume threshold. Use a broader term, a longer range or a larger location. If people write the term in more than one way, join the wordings with `+` in one term.

## `related_topics` is always empty

Google withholds related topics from anonymous sessions. Supply a signed-in cookie ([Install](install#a-signed-in-session-optional)) or use `related_queries`.

## A value changed between two identical requests

Google Trends is computed from a sample of searches. Within the cache lifetime the same answer is returned; after it, a point or two of difference is normal.

## Trending Now is empty for a region

Trending Now covers about 125 countries and some regions. Try the country code instead of the region.

## "Could not reach Google Trends"

The network, a firewall or the proxy. `curl -I https://trends.google.com/trending/rss?geo=US` from the same machine shows whether Google Trends is reachable at all. Where it is blocked, set `GTRENDS_PROXY`.

## Running without a network

`GTRENDS_OFFLINE=1` answers from the cache only. The local tools (`trending_history`, `history_sql`, the watchlist) never need a network.
