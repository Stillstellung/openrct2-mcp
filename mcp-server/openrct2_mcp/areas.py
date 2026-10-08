"""Named map areas ("East Gardens", "Lookout Hill") stored per park.

Areas are saved as JSON in the OpenRCT2 user folder
(``<user dir>/openrct2-mcp/areas/<park key>.json``), so they belong to the
player's park, survive server restarts and stay out of the repo. An area is one
or more rectangles; tools that take a rectangle accept ``area=<name>`` and use
its bounding box. Names match without regard to case.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from openrct2_mcp.paths import openrct2_user_dir


def park_key(park_name: str | None, scenario_file: str | None) -> str:
    """Stable file-name-safe key for a park (park name plus scenario file)."""
    raw = f"{park_name or 'park'}-{Path(scenario_file or 'scenario').stem}"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "park"


def store_path(key: str) -> Path:
    return openrct2_user_dir() / "openrct2-mcp" / "areas" / f"{key}.json"


def load(key: str) -> dict[str, dict[str, Any]]:
    path = store_path(key)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {a["name"]: a for a in data.get("areas", []) if "name" in a}


def save(key: str, areas: dict[str, dict[str, Any]]) -> Path:
    path = store_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"park": key, "areas": sorted(areas.values(), key=lambda a: a["name"].lower())}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _find(areas: dict[str, dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = name.strip().lower()
    for area_name, area in areas.items():
        if area_name.lower() == wanted:
            return area
    return None


def _norm_rect(x1: int, y1: int, x2: int, y2: int) -> list[int]:
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def define(key: str, name: str, x1: int, y1: int, x2: int, y2: int, *, notes: str = "", append: bool = False) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("area name is required")
    areas = load(key)
    existing = _find(areas, name)
    rect = _norm_rect(x1, y1, x2, y2)
    if existing is not None and append:
        if rect not in existing["rects"]:
            existing["rects"].append(rect)
        if notes:
            existing["notes"] = notes
        area = existing
    else:
        if existing is not None:
            areas.pop(existing["name"])
        area = {"name": name, "rects": [rect], "notes": notes, "created": time.strftime("%Y-%m-%d %H:%M")}
        areas[name] = area
    save(key, areas)
    return area


def remove(key: str, name: str) -> bool:
    areas = load(key)
    existing = _find(areas, name)
    if existing is None:
        return False
    areas.pop(existing["name"])
    save(key, areas)
    return True


def bbox(area: dict[str, Any]) -> tuple[int, int, int, int]:
    rects = area["rects"]
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def contains(area: dict[str, Any], x: int, y: int) -> bool:
    return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in area["rects"])


def resolve_area(key: str, name: str) -> tuple[int, int, int, int]:
    """Bounding box of a named area; raises with the known names if missing."""
    areas = load(key)
    area = _find(areas, name)
    if area is None:
        known = ", ".join(sorted(areas)) or "none defined yet (define_area_tool)"
        raise ValueError(f"No area named {name!r}. Known areas: {known}")
    return bbox(area)


def area_tiles(key: str, name: str) -> set[tuple[int, int]]:
    area = _find(load(key), name)
    if area is None:
        raise ValueError(f"No area named {name!r}")
    return {(x, y) for r in area["rects"] for x in range(r[0], r[2] + 1) for y in range(r[1], r[3] + 1)}


def area_marks(key: str) -> list:
    from openrct2_mcp.map_render import AreaMark

    return [AreaMark(a["name"], [tuple(r) for r in a["rects"]]) for a in load(key).values()]


def suggest(index: dict[int, Any], model, *, open_sites: list[dict[str, Any]] | None = None, margin: int = 2) -> list[dict[str, Any]]:
    """Candidate areas: the surroundings of each ride, plus open-land sites if given.

    open_sites are find_open_land candidates ({"origin": [x, y], "size": [w, h]}).
    """
    out = []
    for loc in sorted(index.values(), key=lambda r: r.ride):
        if loc.is_stall or loc.bbox is None:
            continue
        x1, y1, x2, y2 = loc.bbox
        out.append({"name": f"around {loc.name or f'ride {loc.ride}'}",
                    "rect": [x1 - margin, y1 - margin, x2 + margin, y2 + margin], "source": "ride"})
    for i, site in enumerate(open_sites or []):
        (x, y), (w, h) = site["origin"], site["size"]
        out.append({"name": f"open land {i + 1}", "rect": [x, y, x + w - 1, y + h - 1], "source": "open_land"})
    return out
