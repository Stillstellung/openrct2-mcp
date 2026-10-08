"""Readable messages for the ride-builder plugin's numeric track placement errors.

The plugin rejects a failed ``trackplace`` with "Failed to place track piece: <n>",
where <n> is the raw OpenRCT2 ``GameActions::Status`` code. Numbering follows
pyrct2's ActionStatus (copied from OpenRCT2's GameActionResult.h). Two codes were
confirmed in play: 2 for a chain lift on 60-degree track on a Looping Roller
Coaster, and 9 for track passing through the land surface ("Raise or lower land
first"). The other descriptions follow the enum names and are not confirmed live.
"""

from __future__ import annotations

import re
from typing import Any

from pyrct2.errors import ActionStatus

STATUS_DISALLOWED = int(ActionStatus.DISALLOWED)  # 2
STATUS_NO_CLEARANCE = int(ActionStatus.NO_CLEARANCE)  # 9, terrain in the way

STATUS_MESSAGES: dict[int, str] = {
    0: "Ok",
    1: "InvalidParameters (bad position, height or piece for this ride)",
    STATUS_DISALLOWED: (
        "Disallowed (piece/option not allowed for this ride type, e.g. chain lift on steep track)"
    ),
    3: "GamePaused / GameActionNotFound (action rejected by the game state)",
    4: "InsufficientFunds (not enough cash)",
    5: "NotInEditorMode",
    6: "NotOwned (land not owned by the park)",
    7: "TooLow (below the minimum height)",
    8: "TooHigh (above the ride type's maximum height)",
    STATUS_NO_CLEARANCE: (
        "terrain or scenery in the way (track crosses the land surface; "
        "lower the land or move the piece)"
    ),
    10: "ItemAlreadyPlaced",
    11: "NotClosed (close the ride first)",
    12: "Broken",
    13: "NoFreeElements (map element limit reached)",
}

STATUS_HINTS: dict[int, str] = {
    STATUS_DISALLOWED: "drop the chain lift on steep pieces or use a piece this ride type supports",
    6: "buy the land (buy_land_tool) or move the origin",
    8: "lower the design or the origin z",
    STATUS_NO_CLEARANCE: (
        "lower the land at the failing piece, retry with excavate=true, or move the piece"
    ),
}

_TRACK_FAIL_RE = re.compile(r"Failed to place track piece: (\d+)\b")
_PIECE_RE = re.compile(r"Failed at piece (\d+) \(track_type (\d+)\)")


def parse_track_failure(text: str) -> dict[str, Any] | None:
    """{code, piece_index, track_type} from a plugin error string, or None.

    ``piece_index`` is 0 when the message has no "Failed at piece" prefix (the
    plugin omits it when the first piece fails).
    """
    m = _TRACK_FAIL_RE.search(text or "")
    if not m:
        return None
    piece = _PIECE_RE.search(text)
    return {
        "code": int(m.group(1)),
        "piece_index": int(piece.group(1)) if piece else 0,
        "track_type": int(piece.group(2)) if piece else None,
    }


def describe_status(code: int) -> str:
    return STATUS_MESSAGES.get(int(code), "unknown status")


def explain_track_error(text: str) -> str:
    """Append the status name to "Failed to place track piece: <n>" (idempotent)."""
    if not text or "[status " in text:
        return text

    def repl(m: re.Match[str]) -> str:
        code = int(m.group(1))
        return f"{m.group(0)} [status {code}: {describe_status(code)}]"

    return _TRACK_FAIL_RE.sub(repl, text)


def track_error_hint(text: str) -> str | None:
    """A short next step for a track placement failure, when one is known."""
    failure = parse_track_failure(text)
    if failure is None:
        return None
    hint = STATUS_HINTS.get(failure["code"])
    if hint is None:
        return None
    return f"piece {failure['piece_index']}: {hint}"
