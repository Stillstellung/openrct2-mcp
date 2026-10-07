"""Locate the OpenRCT2 user data directory (config.ini, plugin/, save/) per OS."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _windows_documents() -> Path:
    """The user's Documents folder, honoring folder redirection (e.g. OneDrive)."""
    import ctypes

    csidl_personal = 5
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl_personal, None, 0, buf) == 0 and buf.value:
        return Path(buf.value)
    return Path.home() / "Documents"


def openrct2_user_dir() -> Path:
    """OpenRCT2 user data directory, overridable with OPENRCT2_USER_PATH."""
    override = os.environ.get("OPENRCT2_USER_PATH")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return _windows_documents() / "OpenRCT2"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenRCT2"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "OpenRCT2"


def openrct2_config_path() -> Path:
    return openrct2_user_dir() / "config.ini"
