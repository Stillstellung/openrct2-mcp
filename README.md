# OpenRCT2 MCP AI Park Control

[![Tests](https://github.com/BenDaSpur/openrct2-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/BenDaSpur/openrct2-mcp/actions/workflows/test.yml)

Bridge a **running** OpenRCT2 game to Claude Code (or any MCP client) so AI agents can read park state, optimize parks, and build roller coasters. Runs on Windows and macOS.

**Quick links:** [License](LICENSE) · [Issues](https://github.com/BenDaSpur/openrct2-mcp/issues) · [Third-party notices](NOTICE)

## Prerequisites

- OpenRCT2 **0.5.x** with RCT2 game files installed (run it once so `config.ini` exists)
- Python **3.11+** (on Windows, a python.org install is preferred over the Microsoft Store build)
- [Claude Code](https://code.claude.com/) (or another MCP client)

Tested against OpenRCT2 v0.5.1 on macOS and v0.5.5 on Windows 11.

## Quick setup

Close OpenRCT2 before running the install script: the game rewrites `config.ini` when it exits. The script is safe to re-run, for example after pulling changes to `ride-builder.js`.

### Windows (PowerShell)

```powershell
# 1. venv, MCP server, openrct2-bridge + ride-builder plugins, hot reload, .mcp.json
#    -OpenRCT2Path: your OpenRCT2 folder (defaults to C:\Program Files\OpenRCT2)
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -OpenRCT2Path "D:\Games\OpenRCT2"

# 2. Launch OpenRCT2 and load a park, then verify both plugins answer
.venv\Scripts\python.exe scripts\check_connection.py

# Optional: automated headless smoke test (close the game window first)
.venv\Scripts\python.exe scripts\verify-e2e.py
```

### macOS

```bash
# 1. venv, MCP server, openrct2-bridge + ride-builder plugins, hot reload, .mcp.json
chmod +x scripts/*.sh
./scripts/install-bridge.sh

# 2. Launch OpenRCT2 and load a park, then verify both plugins answer
./scripts/check-connection.sh

# Optional: automated headless smoke test (close the game window first)
.venv/bin/python scripts/verify-e2e.py
```

The bridge listens on port 20020 and ride-builder on 20021 (both bind to 127.0.0.1 only). Run **one** OpenRCT2 instance at a time: on Windows a second instance can bind the same ports instead of moving to the next free one.

## Claude Code

The install script writes `.mcp.json` in the repo root (gitignored, because it holds the absolute path to this machine's venv interpreter). See [`.mcp.json.example`](.mcp.json.example) for its shape; on macOS the command is `.venv/bin/python`.

1. Launch OpenRCT2 and load a park.
2. Run `claude` in the repo root and approve the `openrct2` project MCP server when prompted.
3. Run `/mcp` to confirm `openrct2` is connected, then ask for `openrct2_status`.

The server reconnects on its own when the game is closed and relaunched. Use `/mcp` to restart the server itself, for example after pulling changes to its Python code.

The [park director skill](.claude/skills/openrct2-park-director/SKILL.md) is picked up automatically from `.claude/skills/`.

**Note:** End-to-end tools (live park mutations, vision capture, coaster placement) require a running OpenRCT2 instance with plugins loaded. CI runs offline unit tests only.

Example prompts:

- *"Use openrct2_status, then get_park_overview and suggest three improvements."*
- *"List rides sorted by excitement and close the worst performer."*
- *"Build a small wooden coaster: coaster_create, then loop coaster_get_valid_pieces / coaster_place_piece until the circuit completes."*

## Architecture

```
Claude Code  --MCP-->  Python MCP server  --TCP-->  openrct2-bridge (park ops)
                                                `--TCP-->  ride-builder (coasters)
```

| Component | Role |
|-----------|------|
| [openrct2-bridge](https://github.com/MaukWM/openrct2-bridge) | 82 game actions, 77 state queries via [pyrct2](https://pypi.org/project/pyrct2/) |
| `ride-builder.js` | Coaster-specific TCP API (valid pieces, place track, test ratings), bulk map snapshots and the map change feed |
| `mcp-server/openrct2_mcp` | Curated MCP tools wrapping both plugins |

## MCP tools

### Park optimization

| Tool | Description |
|------|-------------|
| `openrct2_status` | Connection health for bridge + ride-builder |
| `get_park_overview` | Rating, guests, cash, scenario objective |
| `list_rides` | All rides with ratings and profit (fast per-ride queries) |
| `get_ride` | One ride by id |
| `get_park_messages` | Complaints and awards |
| `get_guest` | One guest by entity id |
| `list_guests` | Guest count by default; `full_scan=true` for samples (slow) |
| `place_flat_ride` | Place flat rides (e.g. `gentle.MERRY_GO_ROUND`) |
| `place_stall` | Place stalls (e.g. `stall.DRINKS_STALL`) |
| `manage_paths` | Paths, queues, benches, bins |
| `manage_staff` | Hire staff, list, set patrol/orders (by staff_id) |
| `list_staff_tool` / `set_staff_patrol_tool` | Per-staff patrol management |
| `assign_staff_to_path_corridor_tool` | Patrol box covering path tiles |
| `optimize_staff_coverage_tool` | Hire + assign staff along main paths |
| `park_health_report_tool` | Complaints, low satisfaction, recommendations |
| `get_map_bounds_tool` / `get_map_region_tool` | Park-scale spatial grids (up to 64x64, tile_z heights, `area=`) |
| `get_path_graph_tool` / `find_buildable_loop_tool` | Path graph and build surveys |
| `apply_theme_preset_tool` | Theme along paths (see `themes/cute.json`) |
| `place_small_scenery_tool` / `paint_terrain_tool` | Decoration and terrain |
| `set_ride_price` / `open_ride` / `close_ride` | Ride operations |
| `list_refurbish_candidates_tool` | Rank rides needing refurbish by downtime **and** reliability (Maintenance tab metrics) |
| `refurbish_ride_tool` | Renew a ride (resets age/reliability; closes and waits for guests by default) |
| `set_park_settings` | Entrance fee, open/close park |
| `advance_time` | Step simulation ticks |
| `get_research` | Research state |
| `guest_thought_summary_tool` / `guest_density_tool` | Every guest's needs and thoughts in one scan; crowding heatmap on the park map |
| `find_installed_objects_tool` / `load_object_tool` | Add installed objects the scenario left out (e.g. an ATM), regardless of research |
| `ride_setting_tool` | Read or change a ride setting (lift speed, circuits, waiting times) with the values that ride allows |
| `buy_land_tool(dry_run=true)` / `terraform_region_tool(dry_run=true)` | Price land and terraforming with the game's own query before paying |

### Coaster building

| Tool | Description |
|------|-------------|
| `coaster_create` | Start a new coaster shell |
| `coaster_get_valid_pieces` | Legal next track pieces |
| `coaster_place_piece` | Place validated track (`z_is_train_entry` for endpoint Z) |
| `coaster_place_next_piece` | Place next piece from valid endpoint (Z-safe) |
| `coaster_build_perimeter` | Build flexible perimeter loop along guide ring (detours around obstacles) |
| `coaster_obstacle_map_tool` | Perimeter obstacle heatmap (paths, track, scenery, slopes, unowned ring stats) |
| `coaster_plan_route_tool` | Dry-run simulate route (probe+undo, no permanent build) |
| `coaster_plan_ahead_tool` | Beam search 2–4 pieces ahead |
| `coaster_prepare_corridor_tool` | Buy/clear/flatten along guide corridor (`dry_run` default) |
| `coaster_track_context_tool` / `coaster_full_track_context_tool` | Endpoint context with height + collisions |
| `get_tile_height_tool` / `coaster_site_survey_tool` | Ground `tile_z`, slope, flat build sites near x,y |
| `coaster_height_context_tool` / `coaster_probe_piece_tool` | Z vs ground; per-piece collision probe |
| `coaster_start_track_tool` | Create coaster + BeginStation at surveyed tile |
| `coaster_rank_next_pieces_tool` | Rank pieces by clearance, height fit, and collisions |
| `coaster_undo` | Remove last piece |
| `coaster_finish_station` | Place entrance/exit |
| `coaster_test` | Test ride and return ratings |
| `coaster_stats` | Current excitement/intensity/nausea |
| `coaster_delete` | Demolish coaster |
| `list_loaded_ride_objects` | Valid ride object indices |
| `coaster_find_freeform_sites_tool` | Sweep every station spot in the park for layouts that close a circuit |
| `coaster_generate_freeform_tool` | Wandering layout with drops, helixes and inversions; reports `estimated_cost`, auto-chains low-momentum climbs |
| `coaster_fit_design_tool(dry_run=true)` | Lint and price a design at a target without building |
| `get_ride_trains_tool` | Where each train is and how fast (spot stalls) |
| `coaster_set_chain_lift_tool` | Add or remove chain lifts on built track (fix a stalling train) |

## Coaster workflow

1. `list_loaded_ride_objects` — pick a valid `ride_object` index
2. `coaster_site_survey_tool(x, y)` — find flat `tile_z` and nearby obstacles
3. `coaster_start_track_tool(x, y)` or `coaster_create` + manual station placement
4. Loop: `coaster_rank_next_pieces_tool` or `coaster_probe_piece_tool` → `coaster_place_next_piece`
5. When circuit completes: `coaster_finish_station` → `coaster_test`

Use `get_tile_height_tool` for a single tile; `coaster_height_context_tool` for endpoint Z vs ground.

### Perimeter coaster workflow

1. `coaster_obstacle_map_tool(inset=18)` — survey ring clearance and unowned tiles on the guide ring
2. `coaster_plan_route_tool(inset=18)` — simulate trace without building
3. Optional: `coaster_prepare_corridor_tool(dry_run=true)` then apply with `confirm_destructive=true`
4. `coaster_build_perimeter(dry_run=true)` → `coaster_build_perimeter` — execute build

Terminal debug (no MCP): `.venv/bin/python scripts/plan-coaster-route.py --simulate --inset 18` (Windows: `.venv\Scripts\python.exe scripts\plan-coaster-route.py ...`; set `PYTHONUTF8=1` if output is piped)

## Mapping

Every map read goes through one cached **map model**. The ride-builder plugin
returns the map in 64x64 chunks (the whole 128x128 Forest Frontiers map loads in
about 0.3 s; reading it one tile at a time took about 400 s). It also records
which tiles every game action touches, whether the action came from the player,
the bridge or a plugin. The server checks that change feed at the start of each
tool call and after its own writes, and reloads only the chunks that changed.
Heights are **tile_z** (`baseZ // 8`) everywhere; see `openrct2_mcp/units.py`.

| Tool | What it does |
|------|--------------|
| `render_map_tool` | Top-down map image (plan view, +x right, +y down, tile numbers every 5/10): heights, terraces, water, unowned land, paths, queues, bridges/tunnels, rides with names, doors, trees, flower beds, named areas. Overlays draw planned work; `highlight_ride` outlines a ride, its doors and queue |
| `preview=true` | On `landscape_tool`, `build_maze_tool`, `manage_paths` (line, ramp, tile) and `coaster_generate_freeform_tool`: returns the plan drawn on the map instead of building |
| `get_ride_location_tool` / `list_ride_locations_tool` | Ride footprint, entrance/exit with facing, queue walked along its path edges, the path it joins, and issues (missing doors, doors with no path) |
| `map_checkpoint_tool` / `map_diff_tool` | Freeze a rectangle, build, then list exactly what changed (ground, paths, track, doors, scenery) and what it cost |
| `define_area_tool` / `list_areas_tool` / `remove_area_tool` / `suggest_areas_tool` | Named areas per park; most rectangle tools accept `area="East Gardens"` |
| `get_recent_actions_tool` | Last game actions with player and cost (what did the player just do?) |
| `terraform_region_tool(dry_run=true)` | Prices every tile with the game's own action query before paying |

Areas are stored in `<OpenRCT2 user folder>/openrct2-mcp/areas/<park>.json`.

## Vision (screenshots + spatial context)

The MCP server can **see** the park window while also returning structured map data.

| Tool | What it does |
|------|----------------|
| `capture_game_view` | Screenshot of the OpenRCT2 window (returned as an MCP image) |
| `inspect_area_at_tile` | ASCII path/queue grid + nearby rides + optional screenshot |

**Recommended build loop:**

1. `inspect_area_at_tile(39, 44)` — Triple Corkscrew entrance area (example)
2. Plan paths / queue / ride placement from ASCII map + screenshot
3. Apply changes via `manage_paths`, `place_stall`, `coaster_*`, etc.
4. `capture_game_view` — verify visually

**Windows:** no setup needed. The window is captured with `PrintWindow`, so it works when other windows cover the game, and the game never takes focus from your terminal. Fullscreen OpenRCT2 minimizes itself when it loses focus; `capture_game_view` then shows it briefly without activating it, captures it, and minimizes it again. To keep the game visible beside your terminal, play windowed or turn off **Options → Display → Minimise fullscreen on focus loss**. Headless games have no window to capture.

**macOS setup:** System Settings → Privacy & Security → **Screen Recording** → enable the terminal app running Claude Code. Without this, tools fall back to text-only context.

Set `OPENRCT2_VISION_MAX_WIDTH=1280` (default) to limit screenshot size.

## Agent skill

See [`.claude/skills/openrct2-park-director/SKILL.md`](.claude/skills/openrct2-park-director/SKILL.md) for orchestration workflows (perimeter coasters, cute themes, staff patrol). Claude Code loads it automatically in this repo.

**Reload ride-builder after updates:** re-run the install script (or `./scripts/build-plugins.sh` on macOS), then restart OpenRCT2 or rely on hot reloading.

### Ride operations (P6)

| Tool | Description |
|------|-------------|
| `set_ride_inspection_interval_tool` | Inspection every 10–120 minutes |
| `set_ride_mode_tool` / `set_num_trains_tool` / `set_cars_per_train_tool` | Throughput tuning |
| `optimize_ride_throughput_tool` | Composite fix for struggling rides (`dry_run` supported) |
| `demolish_ride_tool` | Demolish any ride (`confirm_destructive=true`) |
| `manage_paths` | Now supports `remove_tile` and `remove_line` |

### Land, finance, guests (P7–P9)

| Tool | Description |
|------|-------------|
| `find_open_land_tool` / `terraform_region_tool` / `buy_land_tool` / `clear_area_tool` | Land survey and terraforming |
| `get_finance_summary_tool` / `optimize_park_pricing_tool` / `start_marketing_campaign_tool` | Business loop |
| `fund_research_tool` / `scenario_progress_tool` | Research and objectives |
| `get_complaint_hotspots_tool` / `guest_flow_summary_tool` | Guest intelligence |

### Advanced building and safety (P10–P11)

| Tool | Description |
|------|-------------|
| `coaster_build_along_path_tool` / `import_track_design_tool` | Path-following coasters, clipboard designs |
| `place_ride_at_best_tile_tool` / `extend_queue_tool` | Smart placement |
| `pre_change_snapshot_tool` / `get_action_log_tool` | Safety checkpoints and action journal |
| Destructive tools | Require `confirm_destructive=true`; composites support `dry_run=true` |

## Performance

Large parks (1000+ guests, many rides) make **bulk** bridge queries slow or prone to timeout.

The MCP server avoids the worst patterns:

| Slow (avoid) | Fast (used instead) |
|--------------|---------------------|
| `rides` (all at once) | `listAllRides` + `rides?id=N` per ride |
| `guests` (all peeps) | `park.guests` count + `get_guest(id)` |
| `game.state.park()` for overview | Scalar `park.name`, `park.rating`, `park.cash`, … |
| Tile-by-tile map scans (about 25 ms per tile) | Cached map model: 64x64 snapshots plus a change feed (`map_model.py`) |
| Footpath scan (fill benches/bins) | `get_elements_by_type("footpath")` bulk first; tile chunk fallback |

`list_guests` defaults to **guest count only**. Pass `full_scan=true` only if you accept a long wait.

Reload the MCP server (`/mcp` in Claude Code) after pulling performance updates.

## Configuration

Environment variables for the MCP server (set in the `env` block of `.mcp.json`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENRCT2_BRIDGE_PORT` | `20020` | First bridge plugin TCP port to scan |
| `OPENRCT2_RIDE_BUILDER_PORT` | `20021` | First ride-builder TCP port to scan |
| `OPENRCT2_BRIDGE_TIMEOUT` | `30` | Per-request bridge timeout (seconds) |
| `OPENRCT2_RIDE_BUILDER_TIMEOUT` | `15` | Per-request ride-builder timeout (seconds) |
| `OPENRCT2_VISION_MAX_WIDTH` | `1280` | Screenshot width limit (pixels) |
| `OPENRCT2_USER_PATH` | per OS (below) | OpenRCT2 user data folder (also read by the install scripts) |

Used by the install scripts and `verify-e2e.py`:

| Variable | Purpose |
|----------|---------|
| `PYRCT2_OPENRCT2_PATH` | OpenRCT2 binary for `pyrct2 setup` and headless launches. `install-windows.ps1` sets it from `-OpenRCT2Path` (it must be `openrct2.com` on Windows). |

OpenRCT2 `config.ini` location:

- **Windows:** `Documents\OpenRCT2\config.ini`
- **macOS:** `~/Library/Application Support/OpenRCT2/config.ini`
- **Linux:** `~/.config/OpenRCT2/config.ini`

The install scripts enable plugin hot reloading:

```ini
[plugin]
enable_hot_reloading = true
```

## Contributing

1. Run the install script for your OS (see [Quick setup](#quick-setup))
2. `pytest mcp-server/tests -q` — offline unit tests (no OpenRCT2 required); CI runs them on Ubuntu and Windows
3. `scripts/verify-e2e.py` with the venv interpreter — optional headless smoke test

## Development

```bash
# Run the MCP server manually (stdio)
.venv/bin/python -m openrct2_mcp          # Windows: .venv\Scripts\python.exe -m openrct2_mcp

# Re-install ride-builder after edits (also re-runs the other post-install steps)
.venv/bin/python -m openrct2_mcp.install  # Windows: .venv\Scripts\python.exe -m openrct2_mcp.install
# Reload plugin in-game (hot reload) or restart OpenRCT2
```

## Safety notes

- Building tools pause the game before mutations.
- `build_in_pause_mode` cheat is enabled on connect for automation.
- Coaster tools require the ride-builder plugin; park tools require openrct2-bridge.

## License

[MIT](LICENSE). See [NOTICE](NOTICE) for third-party attribution. Ride-builder plugin derived from [openrct2-ridecreation-api](https://github.com/markusklock/openrct2-ridecreation-api).
