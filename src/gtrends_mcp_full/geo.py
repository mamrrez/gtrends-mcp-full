"""Finding location codes and category ids in the trees Google publishes.

Both trees come from the Trends website's own pickers and are cached for a
month. A location code is the path through the tree joined with ``-``:
``US`` → ``US-CA`` → ``US-CA-807``.
"""

from __future__ import annotations

from collections.abc import Iterator


def walk_geo(tree: dict) -> Iterator[dict]:
    """Every location as ``{"code", "name", "path", "depth"}``, countries first."""

    def visit(node: dict, prefix: str, names: list[str], depth: int) -> Iterator[dict]:
        for child in node.get("children") or []:
            if not isinstance(child, dict) or not child.get("id") or not child.get("name"):
                continue  # a location without a code or a name cannot be used
            cid, name = str(child["id"]), str(child["name"])
            code = f"{prefix}-{cid}" if prefix else cid
            path = names + [name]
            yield {"code": code, "name": name, "path": " › ".join(path), "depth": depth}
            yield from visit(child, code, path, depth + 1)

    yield from visit(tree, "", [], 0)


def walk_categories(tree: dict) -> Iterator[dict]:
    """Every category as ``{"id", "name", "path", "depth"}``. A category can sit under several parents."""

    def visit(node: dict, names: list[str], depth: int) -> Iterator[dict]:
        for child in node.get("children") or []:
            if not isinstance(child, dict) or not isinstance(child.get("id"), int) or not child.get("name"):
                continue
            path = names + [str(child["name"])]
            yield {"id": child["id"], "name": str(child["name"]), "path": " › ".join(path), "depth": depth}
            yield from visit(child, path, depth + 1)

    yield from visit(tree, [], 0)


def _rank(name: str, needle: str) -> int | None:
    n = name.casefold()
    if n == needle:
        return 0
    if n.startswith(needle):
        return 1
    if any(word.startswith(needle) for word in n.replace("-", " ").split()):
        return 2
    if needle in n:
        return 3
    return None


def search_geo(tree: dict, query: str, limit: int = 25) -> list[dict]:
    """Locations whose name or code matches ``query``, best match first, shallower first."""
    needle = query.strip().casefold()
    if not needle:
        return []
    hits = []
    for item in walk_geo(tree):
        if item["code"].casefold() == needle:
            rank = -1
        else:
            rank = _rank(item["name"], needle)
        if rank is not None:
            hits.append((rank, item["depth"], item["name"], item))
    hits.sort(key=lambda h: h[:3])
    return [h[3] for h in hits[:limit]]


def geo_children(tree: dict, code: str) -> list[dict]:
    """The locations directly inside ``code`` (regions of a country, metros of a region)."""
    code = code.upper()
    depth = code.count("-") + 1
    return [g for g in walk_geo(tree) if g["depth"] == depth and g["code"].startswith(code + "-")]


def geo_name(tree: dict, code: str) -> str:
    if not code:
        return "Worldwide"
    for item in walk_geo(tree):
        if item["code"] == code:
            return item["path"]
    return code


def search_categories(tree: dict, query: str, limit: int = 25) -> list[dict]:
    needle = query.strip().casefold()
    if not needle:
        return []
    hits, seen = [], set()
    for item in walk_categories(tree):
        rank = 0 if str(item["id"]) == needle else _rank(item["name"], needle)
        if rank is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        hits.append((rank, item["depth"], item["name"], item))
    hits.sort(key=lambda h: h[:3])
    return [h[3] for h in hits[:limit]]


def category_name(tree: dict, cat_id: int) -> str:
    if not cat_id:
        return "All categories"
    for item in walk_categories(tree):
        if item["id"] == cat_id:
            return item["path"]
    return f"category {cat_id}"
