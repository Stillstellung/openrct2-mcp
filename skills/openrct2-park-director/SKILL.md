---
name: openrct2-park-director
description: Orchestrate OpenRCT2 park changes via MCP — survey, plan, build, verify. Use for coaster builds, theming, staff patrol, and optimization goals.
---

# OpenRCT2 Park Director

## Standard workflow

1. `openrct2_status` — confirm bridge + ride-builder connected
2. `get_park_overview` / `park_health_report_tool` — baseline metrics
3. **Build coasters** — survey → plan → build (see below)
4. **Verify** — `capture_game_view` and/or `advance_time` with ticks
5. Remind user to **save the park** before large AI builds

## Coaster build pipeline (survey → plan → build)

1. **Poll** — `coaster_poll_buildable_blocks_tool(x, y, width, height)` → flat owned `(x,y,z)` slots
2. **Plan** — `coaster_plan_build_tool(archetype, mood, near_x?, near_y?)` → site, station, track_z, waypoints, circuit segments, feasibility score (no track placed)
3. **Build** — `coaster_execute_plan_tool(plan_json)` or one-shot `coaster_auto_build_tool(brief=..., archetype=...)`

Review the plan before executing on difficult terrain:

| Field | Meaning |
|-------|---------|
| `coverage_ratio` | Fraction of site tiles buildable at `track_z` |
| `build_strategy` | `rectangle` (≤12×12, high coverage), `creative`, or `perimeter` |
| `feasibility_probe` | Dry-run lift/drop at station — must be `ok: true` |
| `lift_peak_z` / `post_drop_z` / `max_track_z` | Energy budget for piece ranking |
| `station_pad` | Tile budget: station, exit buffer + lift run east, entrance north, exit south |
| `guest_access` | Planned entrance/exit coords + `path_route` to nearest park footpath |
| `errors` | Blockers (probe failed, ring blocked, etc.) |

`coaster_execute_plan_tool` and `coaster_auto_build_tool` now finish e2e when the circuit closes:
1. Optional pad prep (buy/clear on pad bbox)
2. Track build
3. `placeEntranceExit` + footpath along `path_route` + short queue from entrance toward path

## RCT2 coaster design rules (enforced in code)

1. **Station** → flat **exit buffer** → **chain lift** (up-slopes, not flat chain) → **drop** → layout → flat **entry buffer** → **StationEnd**
2. **Lift apex** is the highest point; later hills must stay lower or the train stalls
3. **First drop** after lift provides gravity speed for the rest of the circuit
4. Small high-coverage sites use **`rectangle`** strategy (endpoint-driven corners); larger sites use **`out_and_back`** or other creative recipes inside the bbox
5. Track must stay inside the planned footprint (`max_track_z` caps runaway elevation)

## Coaster auto-play (default)

For creative coaster prompts ("around the park", "super condensed fun", "family coaster"), call:

```
coaster_auto_build_tool(
  brief="user's words",
  archetype="perimeter" | "compact_loop",
  mood="fun" | "family" | "intense",
  max_attempts=5,
)
```

The tool internally: validates `ride_object` → surveys site/inset → builds → tests → retries with **session-scoped cleanup** (`coaster_cleanup_session_rides`). No manual tool-picking loop.

| Prompt flavor | archetype | mood |
|---------------|-----------|------|
| Around the park / perimeter | `perimeter` | `fun` |
| Super condensed / wee thrill / compact coaster | `compact_loop` | `fun` |
| Gentle / kids | `compact_loop` | `family` |

Compact sites ≤12×12 with high coverage default to **`rectangle`** builds. Larger or irregular sites use **creative** layouts. Recipes (rotated on retry): `out_and_back` (default), `zigzag_thrills`, `perimeter_waves`, `spiral_in`, `boomerang`.

**Coaster circuit rules (always):**
1. BeginStation → flat exit buffer → chain **up-slope** lift (≥2 pieces gaining height) → **down-slope** drop → layout → flat entry buffer → StationEnd
2. Rectangle loops steer by **live endpoint** at bbox corners (~2 tile turn margin)
3. Never rank slopes during exit/entry buffers

On failure, report `best_partial`, `ring_unowned`, screenshot via `capture_game_view`, and suggest `buy_land` if unowned ring tiles blocked the build.

## Custom Coaster Designer (AI designs the track from a prompt)

Use when the user wants a **custom** coaster designed to their prompt (not a procedural recipe). The agent writes the DesignSpec pieces itself, validates offline, then fits in-game.

### Workflow (envelope -> design -> lint loop -> fit loop -> save)

1. `coaster_site_envelope_tool(center_x, center_y, radius)` — one compact scan: `kind_rows` map, RLE ground z, `obstacle_tops`, `clear_rects`, `suggested_station`, `height_budget`
2. **Design**: write a DesignSpec v1 JSON from the prompt + envelope (rules below)
3. `lint_coaster_design_tool(design_json, envelope_json)` — offline (0 bridge calls, ~2ms). Fix errors by piece index and re-lint until `ok`
4. `coaster_fit_design_tool(design_json, x, y, z, direction, envelope_json, save_as=...)` — probe -> place -> entrance/exit -> test ride. On `stage: "probe"` failure, the error names the failing piece; adjust and go back to 3
5. Templates: `save_coaster_template_tool` / `list_coaster_templates_tool` / `place_coaster_template_tool(name, x, y, z, direction)` to paste saved designs anywhere

### DesignSpec v1 shape

```json
{"version": 1, "ride_type": 52,
 "pieces": [{"track_type": 2}, {"track_type": 3}, {"track_type": 6, "has_chain_lift": true}, ...],
 "origin": {"x": 84, "y": 31, "z": 14, "direction": 2}}
```

### Hard rules (the linter enforces all of these)

1. First piece is **BeginStation (2)**; include >= 5 station pieces total (2/3 lead + **EndStation (1)** closing the circuit)
2. **Slope continuity**: a piece's `beginSlope` must equal the previous `endSlope` — always transition flat -> `FlatToUp25 (6)` -> `Up25 (4)` -> `Up25ToFlat (9)`; down via `12 -> 10 -> 15`
3. **Bank continuity**: same for banking transitions
4. **Chain to peak**: every up-slope piece before the first drop needs `has_chain_lift: true`; gravity only after the drop
5. **Circuit closes**: simulated endpoint must equal the origin exactly (lint reports the x/y/z/direction delta when it doesn't)
6. Turns (`42` left / `43` right small, `16`/`17` large) are **flat-only**; finish slope transitions first
7. No diagonal pieces in v1 (lint rejects them)
8. z accounting: tile_z units (`baseZ // 8`); Up25 = +2/picece, FlatToUp25/Up25ToFlat = +1
9. **Drop soon after the lift** — long flat cruises at the lift apex stall the test train
   (ratings never settle); put the big descent within a few pieces of the peak, then
   bumps/turns run on drop momentum
10. After placement always check `entrance_exit.ok` in the fit result — auto entrance
    placement fails when one station side is covered by existing footpaths (the tool
    now falls back to manual placement, but verify and connect a guest path to the
    entrance front tile)
11. **Entrance/exit/pathing space is part of the site choice** — lint with the envelope
    enforces >= 2 clear flat ground tiles beside the station at station z
    (`entrance_exit_space` error) and warns (`guest_path_far`) when no existing guest
    path is walkable within ~14 tiles. Pick anchors where a station side faces open
    ground near the path network, and budget a few tiles for the connecting footpath
    (it can pass under track that is 4+ tile_z above the path)

### Piece cheat-sheet (track_type ids)

| id | piece | dz | id | piece | dz |
|----|-------|----|----|-------|----|
| 0 | Flat | 0 | 9 | Up25ToFlat | +1 |
| 1 | EndStation | 0 | 10 | Down25 | -2 |
| 2 | BeginStation | 0 | 12 | FlatToDown25 | -1 |
| 3 | MiddleStation | 0 | 15 | Down25ToFlat | -1 |
| 4 | Up25 | +2 | 42 | small left turn (3-tile, flat) | 0 |
| 5 | Up60 | +8 | 43 | small right turn (3-tile, flat) | 0 |
| 6 | FlatToUp25 | +1 | 16/17 | large left/right turn (5-tile, flat) | 0 |

Full vocabulary: `coaster_list_track_segments_tool` (or `data/track_segments.json`).

### Learn from the pros (204 bundled RCT2 designs)

`list_premade_track_designs_tool` indexes RCT2's official .TD6 designs with stats;
`load_premade_track_design_tool(name)` returns a station-normalized DesignSpec you can
study or place with `coaster_fit_design_tool`. Corpus-derived rules:

- **Median chained lift for inversion coasters: +26 tile_z** (Frightmare +36 / 5 inversions / ex 8.1;
  Great White Wail +37 / ex 8.4). A +16 lift stalls a loop+corkscrew circuit; +20 barely clears it
- **Loops are entered from `FlatToUp25 (6)`** (38 of 50 pro loop entries) right after a big drop
- **Corkscrews chain**: the most common piece before a corkscrew is another corkscrew —
  pros sequence cork-down -> cork-up runs, or enter from `Down25ToFlat (15)` / flat
- **Drops beat inversions for excitement**: Nitro (ex 8.8) has 11 drops and zero inversions;
  Texas Giant (ex 8.4) has 14 drops. Many medium drops > one tall drop
- 89 of 204 pro designs simulate fully in the offline linter (84 close exactly);
  the rest use diagonal pieces — the main v1 vocabulary gap

### Crowded parks

- `kind_rows` legend: `.` clear flat owned, `X` unowned (**never crossable**), `~`/`T`/`P`/`s` flyable when track base z > that tile's `top_z` (see `obstacle_tops`), `/` owned slope (supports OK)
- Keep the **station + entrance/exit on clear ground** tiles; elevate the rest of the circuit over paths/water/rides
- A turn template that closes: station x4, climb `[6, 4..., 9]`, turns `42` x4 with leg flats balanced (x: legA + 2*k_up + 9 = legC), descend `[12, 10..., 15]` into `EndStation`
- If nothing fits, say so and offer: terraform owned slopes, demolish a specific ride (ask first), or load a roomier save

## Safety rules

- **Never** call `deleteAllRides` — it demolishes the entire park
- Only delete agent-created shells: `coaster_cleanup_session_rides` or `coaster_delete(ride_id, confirm_destructive=true)` on a known `ride_id`
- Never demolish existing park rides without explicit user approval
- Prefer `refurbish_ride_tool` for old, unreliable, or breakdown-stuck rides before demolishing or cutting prices
- Use `confirm_destructive=true` for `demolish_ride_tool`, `coaster_delete`, `clear_area_tool`
- Coaster track placement requires **unpaused** game (coaster tools handle this)
- Park mutations (paths, scenery, staff) use **paused** game
- On large parks, avoid `list_guests(full_scan=true)` — use `get_park_messages` and `park_health_report_tool`

## Debug / manual coaster tools

Use low-level survey tools only when auto-build fails or user asks for step-by-step control:

- `coaster_obstacle_map_tool`, `coaster_layer_survey_tool`, `coaster_plan_track_route_tool`
- `coaster_rank_next_pieces_tool`, `coaster_place_next_piece`, `coaster_plan_ahead_tool`
- Composites: `coaster_build_perimeter`, `coaster_build_compact_loop_tool`

## Perimeter coasters (manual fallback)

- Follow the **general outer ring** (inset from map bounds), detouring around obstacles
- Check `ring_unowned` / `ring_owned_clear_ratio` from `coaster_obstacle_map_tool`
- Optional: `coaster_prepare_corridor_tool` to buy land on the guide ring
- CLI offline debug: `scripts/plan-coaster-route.py --simulate --inset 18`

## Compact loops (manual fallback)

`coaster_build_compact_loop_tool(creative=true, recipe="out_and_back")` — classic out-and-back or other recipes in 8×8–12×12. Set `creative=false` for endpoint-driven rectangle loop.

## Non-coaster goals

| User request | Tool sequence |
|--------------|---------------|
| Make it cuter | `get_path_graph_tool` → `apply_theme_preset_tool("cute")` → `capture_game_view` |
| Staff on paths | `get_path_graph_tool` → `optimize_staff_coverage_tool` → `park_health_report_tool` |
| Tune one ride | `get_ride` → `set_ride_price` / inspection via ride settings |

## Vision

- macOS: grant **Screen Recording** to Cursor
- Use `inspect_area_at_tile` for ASCII path grids + optional screenshot
