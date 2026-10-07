"""Post-install steps shared by the Windows and macOS setup scripts.

Run with the project's venv interpreter after `pyrct2 setup`:

    python -m openrct2_mcp.install

It installs the ride-builder plugin, enables plugin hot reloading in OpenRCT2's
config.ini, and registers this server with Claude Code in the repo's .mcp.json.
The game must be closed, because OpenRCT2 rewrites config.ini when it exits.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from openrct2_mcp.paths import openrct2_user_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
RIDE_BUILDER_SRC = REPO_ROOT / "plugins" / "ride-builder" / "src" / "ride-builder.js"
RIDE_BUILDER_DIST = REPO_ROOT / "plugins" / "ride-builder" / "dist" / "ride-builder.js"
MCP_CONFIG = REPO_ROOT / ".mcp.json"
BRIDGE_FILENAME = "openrct2-bridge.js"
MCP_SERVER_NAME = "openrct2"


def set_ini_value(text: str, section: str, key: str, value: str) -> str:
    """Set key = value inside [section], adding the key or section if missing.

    Preserves the file's existing line endings and every other line verbatim.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline) if text else []
    entry = f"{key} = {value}"
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    in_section = False
    for index, line in enumerate(lines):
        header = re.match(r"^\s*\[(.+)\]\s*$", line)
        if header:
            if in_section:  # end of target section without the key: insert before this header
                insert_at = index
                while insert_at > 0 and lines[insert_at - 1].strip() == "":
                    insert_at -= 1
                lines.insert(insert_at, entry)
                return newline.join(lines)
            in_section = header.group(1).strip() == section
            continue
        if in_section and key_pattern.match(line):
            lines[index] = entry
            return newline.join(lines)

    insert_at = len(lines)
    while insert_at > 0 and lines[insert_at - 1] == "":
        insert_at -= 1
    lines[insert_at:insert_at] = [entry] if in_section else [f"[{section}]", entry]
    if not lines or lines[-1] != "":
        lines.append("")  # keep a trailing newline
    return newline.join(lines)


def merge_mcp_config(existing: dict, python: str) -> dict:
    """Return existing .mcp.json content with this server's entry added or replaced."""
    config = dict(existing)
    servers = dict(config.get("mcpServers") or {})
    servers[MCP_SERVER_NAME] = {
        "type": "stdio",
        "command": python,
        "args": ["-m", "openrct2_mcp"],
        "env": {
            "PYTHONUTF8": "1",
            "OPENRCT2_BRIDGE_PORT": "20020",
            "OPENRCT2_RIDE_BUILDER_PORT": "20021",
        },
    }
    config["mcpServers"] = servers
    return config


def _install_bridge(plugin_dir: Path) -> Path:
    target = plugin_dir / BRIDGE_FILENAME
    if target.exists():
        return target
    # pyrct2 always installs to the OS default folder; copy it if OPENRCT2_USER_PATH points elsewhere.
    from pyrct2.paths import get_plugin_dir

    default = get_plugin_dir() / BRIDGE_FILENAME
    if default.exists():
        shutil.copy2(default, target)
        return target
    raise SystemExit(
        f"{BRIDGE_FILENAME} is not installed in {plugin_dir}. "
        "Run `pyrct2 setup` first (on a portable OpenRCT2 install, set PYRCT2_OPENRCT2_PATH "
        "to its openrct2.com, or the openrct2 binary on macOS/Linux) and check its output."
    )


def main() -> None:
    user_dir = openrct2_user_dir()
    plugin_dir = user_dir / "plugin"
    config_ini = user_dir / "config.ini"
    print(f"OpenRCT2 user folder: {user_dir}")

    plugin_dir.mkdir(parents=True, exist_ok=True)
    print(f"openrct2-bridge:      {_install_bridge(plugin_dir)}")

    RIDE_BUILDER_DIST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RIDE_BUILDER_SRC, RIDE_BUILDER_DIST)
    shutil.copyfile(RIDE_BUILDER_SRC, plugin_dir / "ride-builder.js")
    print(f"ride-builder:         {plugin_dir / 'ride-builder.js'}")

    if config_ini.exists():
        raw = config_ini.read_bytes().decode("utf-8")
        updated = set_ini_value(raw, "plugin", "enable_hot_reloading", "true")
        if updated != raw:
            config_ini.write_bytes(updated.encode("utf-8"))
        print("config.ini:           [plugin] enable_hot_reloading = true")
    else:
        print(f"config.ini:           not found at {config_ini}; run OpenRCT2 once, then re-run this step")

    existing = json.loads(MCP_CONFIG.read_text(encoding="utf-8")) if MCP_CONFIG.exists() else {}
    MCP_CONFIG.write_text(
        json.dumps(merge_mcp_config(existing, sys.executable), indent=2) + "\n", encoding="utf-8"
    )
    print(f"Claude Code config:   {MCP_CONFIG} (server '{MCP_SERVER_NAME}' -> {sys.executable})")


if __name__ == "__main__":
    main()
