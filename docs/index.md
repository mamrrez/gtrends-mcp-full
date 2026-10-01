---
title: Home
nav_order: 1
description: Connect Google Trends to Claude, Cursor, Windsurf or VS Code — Trending Now, interest over time, seasonality and share of search, with no API key.
permalink: /
---

# gtrends-mcp-full
{: .fs-9 }

Google Trends for your AI assistant. No API key, no browser, no sign-in.
{: .fs-6 .fw-300 }

[Install](install){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 } [Project page](https://hasanpour.com/tools/google-trends-mcp/){: .btn .fs-5 .mb-4 .mb-md-0 .mr-2 } [View on GitHub](https://github.com/mamrrez/gtrends-mcp-full){: .btn .fs-5 .mb-4 .mb-md-0 }

---

## What it does

Ask in plain language and get an answer, not a chart to interpret:

- *What's trending in Germany right now, and which of it is about technology?*
- *Is "heat pump" growing in the UK? When does it peak, and when should the content be live?*
- *What is Toyota's share of search against Honda, Ford and Tesla?*
- *Rank these 20 car brands by search interest.*

Behind those answers are 32 tools in five groups:

| Group | What is in it |
|---|---|
| **Trending now** | The full Trending Now list with search volumes, the news behind a trend, trends shared across countries, trends that match your subjects |
| **Explore** | Interest over time, by region, related queries and topics, two periods or several places side by side |
| **Analysis** | Momentum, seasonality, a content calendar, share of search, more than five terms on one scale, spikes, daily history over years, keyword ideas |
| **History** | Trending Now snapshots kept past Google's one week, a watchlist, read-only SQL |
| **Lookups** | Location codes, category ids, topic ids, an endpoint health check |

## Two things that make it different to use

**It goes past the website's limits.** Five terms per comparison becomes twenty-five. Nine months of daily data becomes years. One week of Trending Now becomes as long as you keep snapshotting.

**It is built to keep working.** The endpoints behind the Trends website are rate-limited. The server paces itself, keeps one long-lived session, backs off instead of hammering, and answers from its cache — saying so — when Google will not. See [Rate limits](rate-limits).

## Three steps

1. [Install it](install) and add it to your client.
2. Ask a question. There is nothing to configure.
3. Read [Reading the numbers](usage#reading-the-numbers) once — it is the difference between using Trends well and misreading it.
