## What this changes

<!-- One or two sentences. -->

## Why

<!-- Why this change is needed. -->

## Checklist

- [ ] `ruff check src tests` passes
- [ ] `pytest` passes (no network needed)
- [ ] A test was added or updated for the change
- [ ] `python scripts/gen_tools_doc.py` was run if a tool's signature or docstring changed
- [ ] A line was added to CHANGELOG.md

## Endpoint changes

If this PR follows a change in a Google Trends endpoint, include the output of `gtrends-mcp-full doctor` before and after, and update the fake in `tests/conftest.py` to the new wire format.
