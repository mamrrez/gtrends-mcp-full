#!/usr/bin/env python3
"""Generate docs/tools.md from the server's own tool registry.

Run after changing any tool signature or docstring:  python scripts/gen_tools_doc.py
The page is generated so it can never drift from the code.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("GTRENDS_CONFIG_DIR", str(ROOT / ".tmp-config"))

from gtrends_mcp_full import __version__  # noqa: E402
from gtrends_mcp_full.server import mcp  # noqa: E402

GROUPS = {
    "Trending now": ["trending_now", "trend_details", "trending_feed", "trending_across_countries", "match_trends"],
    "Explore": ["keyword_overview", "interest_over_time", "interest_by_region", "related_queries", "related_topics", "compare_periods", "compare_locations"],
    "Analysis": ["trend_momentum", "seasonality", "content_calendar", "share_of_search", "compare_many", "find_spikes", "daily_history", "keyword_ideas"],
    "History": ["snapshot_trending", "trending_history", "watchlist_add", "watchlist_remove", "watchlist_report", "history_sql"],
    "Lookups and utilities": ["find_location", "find_category", "find_topic", "get_capabilities", "check_endpoints", "clear_cache"],
}
LOCAL = {"trending_history", "history_sql", "watchlist_add", "watchlist_remove", "get_capabilities", "clear_cache"}


def _type(p: dict) -> str:
    if "type" in p:
        return p["type"]
    return "/".join(x.get("type", "") for x in p.get("anyOf", []) if x.get("type") != "null") or "any"


def main() -> None:
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    listed = [n for names in GROUPS.values() for n in names]
    missing = set(tools) - set(listed)
    if missing or len(listed) != len(set(listed)) or set(listed) - set(tools):
        raise SystemExit(f"GROUPS is out of step with the server: missing {sorted(missing)}, unknown {sorted(set(listed) - set(tools))}")
    out = [
        "---", "title: Tool reference", "nav_order: 4",
        "description: Every Google Trends tool with its parameters, generated from the server itself.", "---", "",
        "# Tool reference", "{: .no_toc }", "",
        f"{len(tools)} tools in gtrends-mcp-full {__version__}. This page is generated from the code by `scripts/gen_tools_doc.py`.",
        "", "Tools marked **local** never contact Google: they work on the data file on your machine.", "",
        "## Table of contents", "{: .no_toc .text-delta }", "", "1. TOC", "{:toc}", "",
    ]  # fmt: skip
    for group, names in GROUPS.items():
        out += ["---", "", f"## {group}", ""]
        for name in names:
            t = tools[name]
            desc = (t.description or "").strip()
            out.append(f"### `{name}`" + (" — local" if name in LOCAL else ""))
            out += ["", desc, ""]
            props = (t.parameters or {}).get("properties", {})
            req = set((t.parameters or {}).get("required", []))
            if props:
                out += ["| Parameter | Type | Default |", "|---|---|---|"]
                for pname, p in props.items():
                    default = "**required**" if pname in req else f"`{json.dumps(p.get('default'), ensure_ascii=False)}`"
                    out.append(f"| `{pname}` | {_type(p)} | {default} |")
                out.append("")
    (ROOT / "docs" / "tools.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote docs/tools.md with {len(tools)} tools")


if __name__ == "__main__":
    main()
