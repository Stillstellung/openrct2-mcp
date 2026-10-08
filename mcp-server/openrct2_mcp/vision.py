"""Capture OpenRCT2 window screenshots for AI vision workflows (macOS and Windows)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp.server.fastmcp.utilities.types import Image

_DEFAULT_MAX_WIDTH = int(os.environ.get("OPENRCT2_VISION_MAX_WIDTH", "1280"))


class VisionCaptureError(RuntimeError):
    """Raised when the game window cannot be captured."""


def _new_capture_path() -> Path:
    fd, name = tempfile.mkstemp(prefix="openrct2-mcp-", suffix=".png")
    os.close(fd)
    return Path(name)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a macOS helper; stdin is detached so it never reads the MCP stdio pipe."""
    try:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise VisionCaptureError(f"{args[0]} could not run: {exc}") from exc


def _find_window_id() -> int | None:
    """Return the CGWindowID for the frontmost OpenRCT2 window, if any (macOS)."""
    script = """
    tell application "System Events"
        repeat with procName in {"OpenRCT2", "openrct2"}
            if exists process procName then
                tell process procName
                    if (count of windows) > 0 then
                        return id of window 1
                    end if
                end tell
            end if
        end repeat
    end tell
  return ""
    """
    result = _run(["osascript", "-e", script])
    window_id = result.stdout.strip()
    if not window_id or not window_id.isdigit():
        return None
    return int(window_id)


def _find_window_bounds() -> tuple[int, int, int, int] | None:
    """Return (x, y, width, height) of the OpenRCT2 window via System Events (macOS)."""
    script = """
    tell application "System Events"
        repeat with procName in {"OpenRCT2", "openrct2"}
            if exists process procName then
                tell process procName
                    if (count of windows) > 0 then
                        set p to position of window 1
                        set s to size of window 1
                        return (item 1 of p) & "," & (item 2 of p) & "," & (item 1 of s) & "," & (item 2 of s)
                    end if
                end tell
            end if
        end repeat
    end tell
    return ""
    """
    result = _run(["osascript", "-e", script])
    parts = result.stdout.strip().split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(p.strip()) for p in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _resize_image(path: Path, max_width: int) -> Path:
    """Downscale with macOS sips to keep MCP payloads reasonable."""
    if max_width <= 0:
        return path
    out = path.with_name(f"{path.stem}.resized{path.suffix}")
    proc = _run(["sips", "-Z", str(max_width), str(path), "--out", str(out)])
    if proc.returncode != 0:
        return path
    return out


def _capture_macos(*, max_width: int, bring_to_front: bool) -> tuple[Path, dict]:
    """Capture via screencapture. Requires Screen Recording permission for the terminal."""
    if bring_to_front:
        _run(["osascript", "-e", 'tell application "OpenRCT2" to activate'])
        time.sleep(0.35)

    window_id = _find_window_id()
    tmp = _new_capture_path()

    if window_id is not None:
        proc = _run(["screencapture", "-x", "-l", str(window_id), str(tmp)])
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            resized = _resize_image(tmp, max_width)
            return resized, {
                "method": "window",
                "window_id": window_id,
                "path": str(resized),
                "bytes": resized.stat().st_size,
            }

    bounds = _find_window_bounds()
    if bounds is not None:
        x, y, w, h = bounds
        proc = _run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(tmp)])
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            resized = _resize_image(tmp, max_width)
            return resized, {
                "method": "window_region",
                "bounds": {"x": x, "y": y, "width": w, "height": h},
                "window_id": window_id,
                "path": str(resized),
                "bytes": resized.stat().st_size,
            }

    # Fallback: full display (less ideal but still useful).
    proc = _run(["screencapture", "-x", str(tmp)])
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        raise VisionCaptureError(
            "Could not capture OpenRCT2. Is the game running and visible? "
            "On macOS, grant Screen Recording permission to the terminal running your MCP client. "
            f"screencapture: {proc.stderr.strip()}"
        )

    resized = _resize_image(tmp, max_width)
    return resized, {
        "method": "fullscreen_fallback",
        "window_id": window_id,
        "path": str(resized),
        "bytes": resized.stat().st_size,
        "note": "OpenRCT2 window not found; captured entire screen instead.",
    }


def capture_game_window(
    *,
    max_width: int = _DEFAULT_MAX_WIDTH,
    bring_to_front: bool = True,
    crop: float = 1.0,
) -> tuple[Path, dict]:
    """Capture the OpenRCT2 game window to a temporary PNG file.

    Returns the image path and metadata dict. Supported on Windows and macOS.
    """
    try:
        if sys.platform == "win32":
            from openrct2_mcp.vision_windows import capture_window_png

            return capture_window_png(
                _new_capture_path(), max_width=max_width, bring_to_front=bring_to_front, crop=crop
            )
        if sys.platform == "darwin":
            return _capture_macos(max_width=max_width, bring_to_front=bring_to_front)
    except VisionCaptureError:
        raise
    except OSError as exc:
        raise VisionCaptureError(f"Screenshot failed: {exc}") from exc
    raise VisionCaptureError(f"Screenshots are not supported on {sys.platform} (Windows and macOS only).")


def capture_game_image(
    *,
    max_width: int = _DEFAULT_MAX_WIDTH,
    bring_to_front: bool = True,
    crop: float = 1.0,
) -> tuple[Image, dict]:
    """Capture and return an MCP Image plus metadata.

    crop < 1 keeps only the centre of the window (Windows; ignored on macOS) so
    detail survives the downscale on large displays.
    """
    path, meta = capture_game_window(max_width=max_width, bring_to_front=bring_to_front, crop=crop)
    return Image(path=path), meta
