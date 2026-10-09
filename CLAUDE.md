# openrct2-mcp

Python MCP server (`mcp-server/openrct2_mcp`) that drives a **running** OpenRCT2 game through two in-game plugins over localhost TCP (newline-delimited JSON):

- **openrct2-bridge** (port 20020+): park actions and state, accessed via the `pyrct2` client. Installed by `pyrct2 setup`, not stored in this repo.
- **ride-builder** (port 20021+): coaster track API, `plugins/ride-builder/src/ride-builder.js` (plain JS, no build step).

## Layout

- `mcp-server/openrct2_mcp/server.py`: all MCP tools (FastMCP, stdio). Tool logic lives in the sibling modules.
- `connection.py`: plugin port scanning, `SESSION` singleton, reconnect after the game restarts.
- `vision.py` (macOS) / `vision_windows.py` (Win32 `PrintWindow`): window screenshots.
- `paths.py`: OpenRCT2 user folder per OS (`OPENRCT2_USER_PATH` override).
- `install.py`: shared post-install steps (`python -m openrct2_mcp.install`).
- `designs/coasters/`, `themes/`: data read relative to the repo root, so use an editable install.

## Commands

- Setup, Windows: `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -OpenRCT2Path <OpenRCT2 folder>`
- Setup, macOS: `./scripts/install-bridge.sh`
- Tests (offline, no game needed): `.venv\Scripts\python.exe -m pytest mcp-server/tests -q` (macOS: `.venv/bin/python -m pytest ...`)
- Live plugin check (game running with a park loaded): `.venv\Scripts\python.exe scripts\check_connection.py`
- Background park poller during long runs: `.venv\Scripts\python.exe scripts\park_watch.py watch --reprice`, then `park_watch.py summary` (doesn't pause the game)
- After editing `ride-builder.js`: `.venv\Scripts\python.exe -m openrct2_mcp.install` (game closed, it edits `config.ini`)
- After editing server Python code: reconnect the server with `/mcp`.

## Rules

- `mcp` is pinned below 2.0: v2 renamed FastMCP. Don't upgrade without porting `server.py`.
- The code imports pyrct2 private modules (`pyrct2._generated`, `pyrct2.world._tile`, `game._connection`); re-run tests when bumping pyrct2.
- Keep Windows and macOS both working: branch on `sys.platform`, use `pathlib`, pass `encoding="utf-8"` to text I/O.
- Never call the ride-builder `deleteAllRides` endpoint. Destructive tools require `confirm_destructive=true`.
- Run only one OpenRCT2 instance at a time; on Windows two instances can bind the same plugin port.
- Edits to OpenRCT2's `config.ini` only stick while the game is closed (it rewrites the file on exit).
