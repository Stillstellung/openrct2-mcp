"""Read RCT2 .TD6 track design files into DesignSpec-compatible data.

The 205 official designs bundled with RCT2 are professionally built coasters —
a reference corpus for the AI designer (piece sequencing, lift/drop energy
ratios, stats) and a free template library via DesignSpec conversion.

Format reference: OpenRCT2 src/openrct2/rct2/T6Importer.cpp + TD6.h.
A .TD6 file is Sawyer RLE-encoded (whole file minus 4-byte checksum).
"""

from __future__ import annotations

import configparser
import struct
from pathlib import Path
from typing import Any

ELEMENTS_OFFSET = 0xA3
CHAIN_LIFT_FLAG = 0x80
MAZE_RIDE_TYPE = 20

OPENRCT2_CONFIG = Path.home() / "Library" / "Application Support" / "OpenRCT2" / "config.ini"


def default_tracks_folder() -> Path | None:
    """RCT2 Tracks folder from the OpenRCT2 config's game_path."""
    try:
        cfg = configparser.ConfigParser()
        cfg.read(OPENRCT2_CONFIG)
        game_path = cfg.get("general", "game_path", fallback="").strip('"')
        if game_path:
            folder = Path(game_path) / "Tracks"
            if folder.is_dir():
                return folder
    except Exception:
        pass
    return None


def _rle_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b & 0x80:  # negative: repeat next byte (257 - b) times
            count = 257 - b
            if i + 1 >= n:
                break
            out.extend(data[i + 1 : i + 2] * count)
            i += 2
        else:  # copy b + 1 literal bytes
            out.extend(data[i + 1 : i + 2 + b])
            i += b + 2
    return bytes(out)


def read_td6(path: str | Path) -> dict[str, Any]:
    """Parse a .TD6 file: header stats, piece list, entrance/exit records."""
    raw = Path(path).read_bytes()
    data = _rle_decode(raw[:-4])  # last 4 bytes are the checksum
    if len(data) < ELEMENTS_OFFSET + 2:
        raise ValueError(f"{path}: decoded data too short ({len(data)} bytes)")

    ride_type = data[0x00]
    vehicle_object = data[0x70:0x80]
    legacy_vehicle = vehicle_object[4:12].decode("ascii", "replace").strip()

    header = {
        "ride_type": ride_type,
        "vehicle_type": data[0x01],
        "ride_mode": data[0x06],
        "number_of_trains": data[0x4C],
        "cars_per_train": data[0x4D],
        "max_speed": struct.unpack_from("b", data, 0x51)[0],
        "average_speed": struct.unpack_from("b", data, 0x52)[0],
        "ride_length": struct.unpack_from("<H", data, 0x53)[0],
        "max_positive_g": data[0x55] / 32.0,
        "max_negative_g": struct.unpack_from("b", data, 0x56)[0] / 32.0,
        "max_lateral_g": data[0x57] / 32.0,
        "inversions": data[0x58] & 0x1F,
        "drops": data[0x59] & 0x3F,
        "highest_drop_height": data[0x5A],
        "excitement": data[0x5B] / 10.0,
        "intensity": data[0x5C] / 10.0,
        "nausea": data[0x5D] / 10.0,
        "space_required": [data[0x80], data[0x81]],
        "lift_hill_speed": data[0xA2] & 0x1F,
        "num_circuits": data[0xA2] >> 5,
        "legacy_vehicle_object": legacy_vehicle,
    }

    pieces: list[dict[str, Any]] = []
    entrances: list[dict[str, int]] = []
    if ride_type != MAZE_RIDE_TYPE:
        i = ELEMENTS_OFFSET
        while i + 1 < len(data) and data[i] != 0xFF:
            track_type = data[i]
            qualifier = data[i + 1]
            piece: dict[str, Any] = {"track_type": track_type}
            if qualifier & CHAIN_LIFT_FLAG:
                piece["has_chain_lift"] = True
            pieces.append(piece)
            i += 2
        i += 1  # skip 0xFF
        while i + 5 < len(data) and data[i] != 0xFF:
            z, direction, x, y = struct.unpack_from("<bBhh", data, i)
            entrances.append({"z": z, "direction": direction, "x": x, "y": y})
            i += 6

    return {"path": str(path), "name": Path(path).stem, **header,
            "piece_count": len(pieces), "pieces": pieces, "entrances": entrances}


STATION_TYPES = {1, 2, 3}


def _normalize_station(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rotate the circular piece list to start at the station block, retyped 2,3..,1.

    TD6 designs often start mid-station (e.g. [Middle, End, lift...]) because the
    saved start point sits inside the station block.
    """
    n = len(pieces)
    if n == 0:
        return pieces
    is_station = [int(p["track_type"]) in STATION_TYPES for p in pieces]
    if not any(is_station):
        return pieces
    # Start of the station block: a station piece whose circular predecessor isn't one.
    start = next(
        (i for i in range(n) if is_station[i] and not is_station[(i - 1) % n]),
        0,
    )
    rotated = [dict(pieces[(start + i) % n]) for i in range(n)]
    run = 0
    while run < n and int(rotated[run]["track_type"]) in STATION_TYPES:
        run += 1
    for i in range(run):
        rotated[i]["track_type"] = 2 if i == 0 else (1 if i == run - 1 and run > 1 else 3)
    return rotated


def td6_to_design_spec(td6: dict[str, Any], *, normalize: bool = True) -> dict[str, Any]:
    """Convert parsed TD6 data to a DesignSpec v1 (origin set at placement time)."""
    pieces = [dict(p) for p in td6["pieces"]]
    if normalize:
        pieces = _normalize_station(pieces)
    return {
        "version": 1,
        "name": td6.get("name"),
        "ride_type": int(td6["ride_type"]),
        "pieces": pieces,
        "source": "td6",
        "td6_stats": {
            k: td6[k]
            for k in ("excitement", "intensity", "nausea", "inversions", "drops",
                      "highest_drop_height", "space_required", "legacy_vehicle_object")
            if k in td6
        },
    }


def list_td6_library(folder: str | Path | None = None) -> list[dict[str, Any]]:
    """Summaries of every parseable .TD6 in the folder (no piece lists)."""
    base = Path(folder) if folder else default_tracks_folder()
    if base is None or not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.[tT][dD]6")):
        try:
            td6 = read_td6(path)
        except Exception as exc:
            out.append({"name": path.stem, "error": str(exc)[:80]})
            continue
        out.append({
            k: td6[k]
            for k in ("name", "ride_type", "piece_count", "excitement", "intensity",
                      "nausea", "inversions", "drops", "highest_drop_height",
                      "space_required", "legacy_vehicle_object")
        })
    return out


def load_td6_design(name: str, folder: str | Path | None = None) -> dict[str, Any]:
    """Load one design by name (case-insensitive stem match) as TD6 data."""
    base = Path(folder) if folder else default_tracks_folder()
    if base is None or not base.is_dir():
        raise ValueError("RCT2 Tracks folder not found; pass folder explicitly")
    want = name.lower()
    for path in base.glob("*.[tT][dD]6"):
        if path.stem.lower() == want:
            return read_td6(path)
    matches = [p.stem for p in base.glob("*.[tT][dD]6") if want in p.stem.lower()]
    if len(matches) == 1:
        return read_td6(base / f"{matches[0]}.TD6")
    raise ValueError(f"design '{name}' not found; close matches: {matches[:8]}")
