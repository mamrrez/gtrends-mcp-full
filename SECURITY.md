# Security policy

## What this server sends and stores

Requests go to `trends.google.com` and nowhere else. They contain what any
visitor to the Trends website sends: the keywords, location and time range
being asked about. There is no telemetry and no hosted service.

Under `~/.config/gtrends-mcp-full/` (the folder, the data file with its `-wal` and `-shm` companions, and the cookie file are all created readable by their owner only):

- `trends.sqlite` — cached answers, the Trending Now snapshots you saved, your
  watchlist. The keywords you looked up are in the cache; treat the file as you
  would your search history.
- `cookies.json` — Google's anonymous session cookie. It identifies a browser
  session, not a person, and holds no login.

## A signed-in cookie is a credential

`GTRENDS_COOKIE` and `GTRENDS_COOKIES_FILE` are optional and unlock one tool
(`related_topics`). Cookies of a signed-in browser are a login to that Google
account: anyone who can read them can act as that account until the session is
revoked. Use an account made for this purpose, keep the file out of any
repository, and revoke the session at
https://myaccount.google.com/device-activity when you are done. The server
sends such cookies only to `trends.google.com` and never writes them to its
own files.

## Defaults that limit damage

- No tool changes anything outside the local data file.
- `history_sql` opens the data file read-only and accepts a single `SELECT`.
- Trend titles, queries and headlines come from the public; they are escaped
  before they reach a Markdown table and labelled as data in the server
  instructions. No tool executes text from the data.
- Credentials are kept out of output: a signed-in cookie or a proxy password is removed from every error message before it reaches a tool result or a log.
- Redirects are never followed, so a session cookie cannot travel to another host.
- `history_sql` cannot open another database file, and (on Python 3.11 and newer) no single value in a result can exceed 100 KB.

## HTTP mode has no authentication

Over stdio the server talks only to the client that started it. Over HTTP (`--transport streamable-http`) **anyone who can reach the port can use every tool**: read the watchlist and the saved history, change the watchlist, and send requests to Google from your connection and session. So:

- it binds to `127.0.0.1` by default, and the Host and Origin headers are always checked, which stops a web page from reaching it through DNS rebinding;
- it refuses to start on any other address unless `GTRENDS_ALLOWED_HOSTS` names the hosts it serves;
- if you expose it, put it behind a reverse proxy that authenticates, and list the proxy's host names there.

## Reporting a vulnerability

Please do **not** open a public issue. Email the maintainer (address on the
GitHub profile of [@mamrrez](https://github.com/mamrrez)) or use GitHub's
private vulnerability reporting on this repository. You will get a reply
within a week.

## Supply chain

Releases are built from tagged commits in this repository. The runtime
dependencies are `mcp` and `requests`.
