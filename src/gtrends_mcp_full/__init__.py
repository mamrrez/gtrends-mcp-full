"""gtrends-mcp-full — a Google Trends MCP server.

Trending Now, interest over time, regions, related searches and the analysis
on top of them, with no API key and no browser. The package is split so the
parts that never touch the network (``timeframes``, ``analysis``, ``matching``,
``format``) can be used and tested on their own.
"""

__version__ = "0.1.1"
