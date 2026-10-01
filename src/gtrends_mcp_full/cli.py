"""``gtrends-mcp-full`` — serve (default), doctor, trending, snapshot, tools."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys

from . import __version__
from .format import printable, short_path


def _utf8_stdout() -> None:
    """Trend titles and tool docs contain non-ASCII text; a cp1252 console would crash on them."""
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _serve(args: argparse.Namespace) -> int:
    from .server import run

    try:
        run(transport=args.transport, host=args.host, port=args.port)
    except ValueError as e:  # e.g. a public bind without GTRENDS_ALLOWED_HOSTS
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def _runtime():
    from .runtime import Runtime

    rt = Runtime()
    rt.client().begin_call(120.0)
    return rt


def _doctor(args: argparse.Namespace) -> int:
    _utf8_stdout()
    try:
        rt = _runtime()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    st = rt.settings
    print(f"gtrends-mcp-full {__version__} · python {sys.version.split()[0]}")
    print(f"  default location: {st.geo or 'worldwide'}\n  language: {st.hl}\n  timezone: {st.tz_label()}")
    print(f"  proxy: {'set' if st.proxy else 'none'}\n  signed-in cookie: {'set' if (st.cookie or st.cookies_file) else 'none'}")
    print(f"  data file: {short_path(st.db_path)}\n")
    if rt.client().cookie_problem:
        print(f"  WARNING: {rt.client().cookie_problem}; continuing as an anonymous session\n")
    if st.offline:
        print("Offline mode is on (GTRENDS_OFFLINE=1); endpoints were not checked.")
        return 0
    results = rt.client().check()
    for name, ok, detail in results:
        print(f"  {'OK    ' if ok else 'FAILED'} {name:<20} {printable(detail)}")
    print(f"\nSession as Google sees it: {rt.client().session_kind()}")
    failed = [r for r in results if not r[1]]
    if failed:
        print(f"\n{len(failed)} of {len(results)} endpoints failed. A 429 is rate limiting (wait, or set GTRENDS_PROXY); a 404 or parsing failure means the endpoint changed.")
        return 3
    print("\nOK — every endpoint answered.")
    return 0


def _trending(args: argparse.Namespace) -> int:
    from .client import TrendsError
    from .format import fmt_volume

    _utf8_stdout()
    try:
        rt = _runtime()
        geo, args.hours = rt.country(args.geo), rt.hours(args.hours)
        trends = rt.client().trending_now(geo, args.hours)
    except (TrendsError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    trends.sort(key=lambda t: (-t["volume"], -t["growth"]))
    if args.json:
        print(json.dumps(trends[: args.limit], ensure_ascii=False, indent=2))
        return 0
    print(f"Trending now in {geo}, past {args.hours} hours ({len(trends)} trends)")
    for t in trends[: args.limit]:
        print(f"  {fmt_volume(t['volume']):>6}  +{t['growth']:>5}%  {'active' if not t['ended'] else 'ended ':<6}  {printable(t['title'])}")
    return 0


def _snapshot(args: argparse.Namespace) -> int:
    """Save Trending Now for some locations. Meant for cron."""
    from .client import TrendsError
    from .tools_history import snapshot

    _utf8_stdout()
    try:
        rt = _runtime()
        codes = rt.geos(args.geos, [rt.country("")], cap=25)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    results, failed = [], 0
    for code in codes:
        try:
            results.append(snapshot(rt.client(), rt.store(), code, args.hours))
        except TrendsError as e:
            failed += 1
            print(f"error: {code}: {e}", file=sys.stderr)
    print(json.dumps({"saved": results, "failed": failed, "db": short_path(rt.settings.db_path)}, ensure_ascii=False))
    return 0 if results else 3


def _tools(args: argparse.Namespace) -> int:
    """Print every tool as Markdown."""
    from .server import mcp

    _utf8_stdout()
    tools = mcp._tool_manager.list_tools()
    print("| Tool | What it does |\n|---|---|")
    for t in sorted(tools, key=lambda t: t.name):
        first = (t.description or "").strip().splitlines()[0]
        print(f"| `{t.name}` | {first} |")
    if not args.brief:
        for t in sorted(tools, key=lambda t: t.name):
            print(f"\n### `{t.name}`\n")
            print((t.description or "").strip())
            props = (t.parameters or {}).get("properties", {})
            req = set((t.parameters or {}).get("required", []))
            if props:
                print("\n| Parameter | Type | Default |\n|---|---|---|")
                for name, p in props.items():
                    typ = p.get("type") or "/".join(x.get("type", "") for x in p.get("anyOf", []) if x.get("type") != "null") or "any"
                    default = "required" if name in req else json.dumps(p.get("default"), ensure_ascii=False)
                    print(f"| `{name}` | {typ} | {default} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gtrends-mcp-full", description="Google Trends MCP server: Trending Now, interest over time, regions, related searches and analysis. No API key.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the MCP server (default)")
    s.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=_serve)

    d = sub.add_parser("doctor", help="check the configuration and every Google Trends endpoint")
    d.set_defaults(fn=_doctor)

    t = sub.add_parser("trending", help="print what is trending now")
    t.add_argument("geo", nargs="?", default="")
    t.add_argument("--hours", type=int, default=24)
    t.add_argument("--limit", type=int, default=25)
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=_trending)

    n = sub.add_parser("snapshot", help="save Trending Now to the local history (for cron)")
    n.add_argument("geos", nargs="?", default="", help="comma-separated location codes, e.g. US,GB,DE")
    n.add_argument("--hours", type=int, default=24)
    n.set_defaults(fn=_snapshot)

    o = sub.add_parser("tools", help="print the tool reference as Markdown")
    o.add_argument("--brief", action="store_true")
    o.set_defaults(fn=_tools)

    argv = list(sys.argv[1:] if argv is None else argv)
    # `serve` is the default: `gtrends-mcp-full` and `gtrends-mcp-full --transport …` both run the server.
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv = ["serve", *argv]
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
