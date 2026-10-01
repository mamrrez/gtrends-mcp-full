---
title: Install
nav_order: 2
description: Install the Google Trends MCP server and add it to Claude Desktop, Claude Code, Cursor, Windsurf or VS Code.
---

# Install
{: .no_toc }

1. TOC
{:toc}

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/) or pip. No Google account, API key or browser.

## Add it to your client

`uvx` fetches the package from PyPI and runs it; there is nothing to download first.

### Claude Code

```sh
claude mcp add gtrends -- uvx gtrends-mcp-full
```

### Claude Desktop, Cursor, Windsurf, VS Code

Add this to the client's MCP configuration file:

```json
{
  "mcpServers": {
    "gtrends": {
      "command": "uvx",
      "args": ["gtrends-mcp-full"],
      "env": {
        "GTRENDS_GEO": "US",
        "GTRENDS_TIMEZONE": "America/New_York"
      }
    }
  }
}
```

The `env` block is optional.

## Check it

```sh
uvx gtrends-mcp-full doctor
```

`doctor` prints the configuration and asks every Google Trends endpoint one question. All eight should say `OK`; on the very first run a chart endpoint may say the session is still being validated, which passes after a minute and a half.

## Install it as a command

To use the command line (`doctor`, `trending`, `snapshot`) or to pin a version:

```sh
uv tool install gtrends-mcp-full     # or: pip install gtrends-mcp-full
gtrends-mcp-full --version
```

### Over HTTP

```sh
gtrends-mcp-full --transport streamable-http --host 127.0.0.1 --port 8000
```

{: .warning }
HTTP mode has no authentication: anyone who can reach the port can use every tool. It serves `127.0.0.1` only, and refuses to start on any other address unless `GTRENDS_ALLOWED_HOSTS` lists the host names it is reached by. If you expose it, put it behind a reverse proxy that authenticates. See [Security](security).

## Configuration

Every setting is optional.

| Variable | Default | What it does |
|---|---|---|
| `GTRENDS_GEO` | worldwide | Default location code (`US`, `IR`, `US-CA`) when a tool is called without one |
| `GTRENDS_HL` | `en-US` | Language of the names Google returns (locations, categories, topics) |
| `GTRENDS_TIMEZONE` | this machine's | IANA timezone for times in results and for hourly data, e.g. `Asia/Tehran` |
| `GTRENDS_PROXY` | none | HTTP(S) proxy URL for requests to Google |
| `GTRENDS_MIN_INTERVAL` | `1.5` | Seconds between requests to Google |
| `GTRENDS_CACHE_TTL` | `3600` | Seconds a chart answer is reused |
| `GTRENDS_TRENDING_TTL` | `300` | Seconds a Trending Now answer is reused |
| `GTRENDS_TIME_BUDGET` | `50` | Seconds one tool call may spend waiting on Google before it reports back |
| `GTRENDS_COOKIE` | none | A signed-in Google session as a raw `Cookie` header |
| `GTRENDS_COOKIES_FILE` | none | The same, as a Netscape `cookies.txt` file |
| `GTRENDS_OFFLINE` | off | Answer from the cache only, never contact Google |
| `GTRENDS_CONFIG_DIR` | `~/.config/gtrends-mcp-full` | Where the data file and session cookie live |
| `GTRENDS_DB_PATH` | `<config dir>/trends.sqlite` | The data file: cache, trending history, watchlist |
| `GTRENDS_USER_AGENT` | a current desktop Chrome | The User-Agent sent to Google |
| `GTRENDS_ALLOWED_HOSTS` | none | Host names allowed in HTTP mode behind a reverse proxy (required to bind to anything but localhost) |

### A signed-in session (optional)

One tool needs it: `related_topics`. Google returns an empty related-topics list to anonymous sessions. To unlock it, export the cookies of a browser signed in to Google — as a `cookies.txt` file (set `GTRENDS_COOKIES_FILE`) or as the `Cookie` header of a request to `trends.google.com` (set `GTRENDS_COOKIE`).

{: .warning }
Those cookies are a login to your Google account. Keep the file private, never commit it, and prefer a throwaway account. Read [Security](security) first.

## Keep a trending history

Google keeps a trend for about a week. Schedule a snapshot to keep your own record:

```cron
0 */6 * * * gtrends-mcp-full snapshot US,GB,DE
```

Then ask *"what trended in Germany last month?"* any time. See [History](history).
