# OpenRCT2 MCP AI Park Control

[![Tests](https://github.com/BenDaSpur/openrct2-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/BenDaSpur/openrct2-mcp/actions/workflows/test.yml)

Bridge a **running** OpenRCT2 game to Cursor (or any MCP client) so AI agents can read park state, optimize parks, and build roller coasters.

**Quick links:** [License](LICENSE) · [Issues](https://github.com/BenDaSpur/openrct2-mcp/issues) · [Third-party notices](NOTICE)

## Prerequisites

- OpenRCT2 **0.5.x** with RCT2 game files installed
- Python **3.11+**
- Node.js **18+** (plugin packaging only)

This repo was tested against OpenRCT2 v0.5.1 on macOS.

## Quick setup

```bash
# 1. Install Python deps + openrct2-bridge plugin
chmod +x scripts/*.sh
./scripts/install-bridge.sh

# 2. Install ride-builder coaster plugin
./scripts/build-plugins.sh

# 3. Launch OpenRCT2, load a park save
#    Check the in-game console for:
#      - Bridge port (starts at 20020)
#      - Ride-builder port (starts at 20021)

# 4. Verify connections (with OpenRCT2 running and a park loaded)
./scripts/check-connection.sh

# Optional: automated headless smoke test
.venv/bin/python scripts/verify-e2e.py
```

## Cursor MCP

Project MCP config lives at [`.cursor/mcp.json`](.cursor/mcp.json) (portable template: [`.cursor/mcp.json.example`](.cursor/mcp.json.example)). Paths use `${workspaceFolder}` and set `PYTHONPATH` to `mcp-server/` so `python -m openrct2_mcp` resolves without a global install. Copy or adapt the example if your editor does not expand `${workspaceFolder}`.

Reload MCP servers in Cursor after OpenRCT2 is running with a loaded park.

**Note:** End-to-end tools (live park mutations, vision capture, coaster placement) require a running OpenRCT2 instance with plugins loaded. CI runs offline unit tests only.

Example prompts:

- *"Use openrct2_status, then get_park_overview and suggest three improvements."*
- *"List rides sorted by excitement and close the worst performer."*
- *"Build a small wooden coaster: coaster_create, then loop coaster_get_valid_pieces / coaster_place_piece until the circuit completes."*

## Architecture

```
Cursor AI  --MCP-->  Python MCP server  --TCP-->  openrct2-bridge (park ops)
                                              `--TCP-->  ride-builder (coasters)
```

| Component | Role |
|-----------|------|
| [openrct2-bridge](https://github.com/MaukWM/openrct2-bridge) | 82 game actions, 77 state queries via [pyrct2](https://pypi.org/project/pyrct2/) |
| `ride-builder.js` | Coaster-specific TCP API (valid pieces, place track, test ratings) |
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
| `get_map_bounds_tool` / `get_map_region_tool` | Park-scale spatial grids |
| `get_path_graph_tool` / `find_buildable_loop_tool` | Path graph and build surveys |
| `apply_theme_preset_tool` | Theme along paths (see `themes/cute.json`) |
| `place_small_scenery_tool` / `paint_terrain_tool` | Decoration and terrain |
| `set_ride_price` / `open_ride` / `close_ride` | Ride operations |
| `refurbish_ride_tool` | Renew a ride (resets age/reliability; closes and waits for guests by default) |
| `set_park_settings` | Entrance fee, open/close park |
| `advance_time` | Step simulation ticks |
| `get_research` | Research state |

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

Terminal debug (no MCP): `.venv/bin/python scripts/plan-coaster-route.py --simulate --inset 18`

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

**macOS setup:** System Settings → Privacy & Security → **Screen Recording** → enable **Cursor** (and Terminal if testing from CLI). Without this, tools fall back to text-only context.

Set `OPENRCT2_VISION_MAX_WIDTH=1280` (default) to limit screenshot size.

## Agent skill

See [`skills/openrct2-park-director/SKILL.md`](skills/openrct2-park-director/SKILL.md) for orchestration workflows (perimeter coasters, cute themes, staff patrol).

**Reload ride-builder after updates:** `./scripts/build-plugins.sh` then restart OpenRCT2 (plugin loads at launch).

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
| Tile-by-tile map scans | Not exposed via MCP (too many round-trips) |
| Footpath scan (fill benches/bins) | `get_elements_by_type("footpath")` bulk first; tile chunk fallback |

`list_guests` defaults to **guest count only**. Pass `full_scan=true` only if you accept a long wait.

Reload the MCP server after pulling performance updates.

## Configuration

Environment variables (set in `.cursor/mcp.json`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENRCT2_BRIDGE_PORT` | `20020` | Bridge plugin TCP port |
| `OPENRCT2_RIDE_BUILDER_PORT` | `20021` | Ride-builder TCP port |
| `OPENRCT2_BRIDGE_TIMEOUT` | `30` | Per-request bridge timeout (seconds) |
| `OPENRCT2_RIDE_BUILDER_TIMEOUT` | `15` | Per-request ride-builder timeout (seconds) |

OpenRCT2 `config.ini` recommendations:

- **macOS:** `~/Library/Application Support/OpenRCT2/config.ini`
- **Linux:** `~/.config/OpenRCT2/config.ini`

Override the user data directory with `OPENRCT2_USER_PATH` (used by `install-bridge.sh`).

```ini
[plugin]
enable_hot_reloading = true
```

`install-bridge.sh` enables hot reloading automatically.

## Contributing

1. `./scripts/install-bridge.sh` — Python venv, bridge plugin, hot reload
2. `./scripts/build-plugins.sh` — ride-builder plugin
3. `pytest mcp-server/tests -q` — offline unit tests (no OpenRCT2 required)
4. `.venv/bin/python scripts/verify-e2e.py` — optional smoke test with OpenRCT2 running

## Development

```bash
# Run MCP server manually
export PYTHONPATH=mcp-server
.venv/bin/python -m openrct2_mcp

# Rebuild ride-builder after edits
./scripts/build-plugins.sh
# Reload plugin in-game (hot reload) or restart OpenRCT2
```

## Safety notes

- Building tools pause the game before mutations.
- `build_in_pause_mode` cheat is enabled on connect for automation.
- Coaster tools require the ride-builder plugin; park tools require openrct2-bridge.

## License

[MIT](LICENSE). See [NOTICE](NOTICE) for third-party attribution. Ride-builder plugin derived from [openrct2-ridecreation-api](https://github.com/markusklock/openrct2-ridecreation-api).
