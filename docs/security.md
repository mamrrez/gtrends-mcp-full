---
title: Security
nav_order: 8
description: What the server stores, what it sends, and how to handle a signed-in cookie.
---

# Security

## What it sends, and where

Requests go to `trends.google.com` and nowhere else. They contain what any visitor to the Trends website sends: the keywords, location and time range being asked about. There is no telemetry and no hosted service.

## What it stores

In `~/.config/gtrends-mcp-full/` (or `GTRENDS_CONFIG_DIR`). The folder and every file in it are created readable only by you:

| File | Contents |
|---|---|
| `trends.sqlite` | cached answers, the Trending Now snapshots you saved, your watchlist and its readings |
| `cookies.json` | Google's anonymous session cookie (`NID`). It identifies a browser session, not a person, and holds no login |

The keywords you look up are in the cache. Treat the file as you would your search history.

## A signed-in cookie

`GTRENDS_COOKIE` and `GTRENDS_COOKIES_FILE` are optional and unlock one tool, `related_topics`. Cookies of a signed-in browser **are a login to that Google account**:

- anyone who reads the file or the environment variable can act as that account until the session is revoked;
- prefer an account made for this purpose, never your main one;
- keep the file outside any repository and out of shared configuration;
- revoke it at <https://myaccount.google.com/device-activity> when you are done.

The server sends such cookies only to `trends.google.com` and never copies them into its own files.

A cookie value or a proxy password is removed from every error message before it reaches a tool result or a log, and redirects are never followed, so a cookie cannot travel to another host.

## HTTP mode has no authentication

Over stdio the server talks only to the client that started it. Over HTTP, **anyone who can reach the port can use every tool**. It therefore binds to `127.0.0.1` by default, always checks the Host and Origin headers, and refuses to start on any other address unless `GTRENDS_ALLOWED_HOSTS` is set. If you expose it, put it behind a reverse proxy that authenticates.

## Text from the public

Trend titles, related queries and news headlines are written by the public. They are escaped before they reach a Markdown table, and the server's instructions tell the model to treat them as data. No tool executes text from the data.

## `history_sql`

Opens the data file read-only and accepts a single `SELECT`. It cannot attach another database, a query is stopped after five seconds, and on Python 3.11 and newer no single value can exceed 100 KB.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting on the repository, or email the maintainer (address on the GitHub profile of [@mamrrez](https://github.com/mamrrez)). You will get a reply within a week.
