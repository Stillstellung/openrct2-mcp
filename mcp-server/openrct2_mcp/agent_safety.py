"""Action journal, destructive confirmations, and dry-run helpers."""

from __future__ import annotations

import time
from typing import Any

_ACTION_LOG: list[dict[str, Any]] = []
_SESSION_RIDE_IDS: list[int] = []
_MAX_LOG = 200


def require_destructive_confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise ValueError(
            f"Destructive action '{action}' requires confirm_destructive=true. "
            "Save your park before proceeding."
        )


def log_action(action: str, details: dict[str, Any], *, dry_run: bool = False) -> None:
    entry = {
        "timestamp": time.time(),
        "action": action,
        "dry_run": dry_run,
        "details": details,
    }
    _ACTION_LOG.append(entry)
    if len(_ACTION_LOG) > _MAX_LOG:
        del _ACTION_LOG[: len(_ACTION_LOG) - _MAX_LOG]


def get_action_log(limit: int = 50) -> list[dict[str, Any]]:
    return list(_ACTION_LOG[-limit:])


def clear_action_log() -> int:
    count = len(_ACTION_LOG)
    _ACTION_LOG.clear()
    return count


def track_session_ride(ride_id: int) -> None:
    """Record a ride created during this MCP session (for scoped cleanup)."""
    if ride_id not in _SESSION_RIDE_IDS:
        _SESSION_RIDE_IDS.append(ride_id)


def get_session_ride_ids() -> list[int]:
    return list(_SESSION_RIDE_IDS)


def clear_session_rides(ride_builder: Any, *, keep_ride_id: int | None = None) -> list[int]:
    """Delete rides created this session. Never touches other park rides."""
    removed: list[int] = []
    for ride_id in list(_SESSION_RIDE_IDS):
        if keep_ride_id is not None and ride_id == keep_ride_id:
            continue
        try:
            ride_builder.call("deleteRide", {"rideId": ride_id})
            removed.append(ride_id)
        except Exception:
            pass
    if keep_ride_id is not None:
        _SESSION_RIDE_IDS[:] = [keep_ride_id] if keep_ride_id in _SESSION_RIDE_IDS else []
    else:
        _SESSION_RIDE_IDS.clear()
    return removed
