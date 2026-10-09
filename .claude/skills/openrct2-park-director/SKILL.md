---
name: openrct2-park-director
description: Run an OpenRCT2 park through the MCP server - open a scenario, read guest thoughts and fix complaints (crowding, cash, dirt, vandalism, toilets), build and tune coasters, landscape, map and verify. Use for any park-management, building, theming or optimization goal.
---

# OpenRCT2 Park Director

## Standard workflow

1. `openrct2_status` — confirm bridge + ride-builder connected **and `park_loaded: true`**. On the
   title screen the plugins still answer from a demo park, so reads mislead and writes are refused
2. `get_park_overview` / `park_health_report_tool` / `guest_thought_summary_tool` — baseline metrics
3. `render_map_tool()` — see the park before planning anything
4. **Build or fix** — see "Running a park" and the coaster sections below
5. **Verify** — `advance_time`, then re-read thoughts / ride stats; `map_diff_tool` for builds
6. Remind user to **save the park** before large AI builds

## Running a park (playbook)

General lessons from a full playthrough (details in `docs/playthrough-forest-frontiers.md`).

**Opening a scenario**
- Read the objective and money first (`get_park_overview`, `get_finance_summary_tool`). Pause,
  set research priorities, open the park on day 1
- First build: one main path from the gate, 4-6 cheap flat rides with doors facing the path,
  food + drinks + restroom, 1 handyman (orders 7) + 1 mechanic (orders 3). Check `orders` after
  hiring: staff with orders 0 never sweep or repair
- A modest first coaster beats a big one; guests come from rating and ride count, and income
  (ride prices) is the early bottleneck, not guests
- Check whether the park charges entry or per ride (`entrance_fee` 0 = pay per ride)

**Read the thought mix, not the rating.** The rating lags dirt by weeks, then falls off a cliff.
`guest_thought_summary_tool` aggregates every guest in about a second:

| Thought | Means | Fix |
|---|---|---|
| `crowded` | dense walking crowds on a tile | `guest_density_tool` heatmap, then a second lane, a bypass beside paths lined with ride doors, or a loop so guests aren't funnelled down one route |
| `not_thirsty` / `not_hungry` | **good**: passed a stall without needing it | nothing (many = plenty of stalls) |
| `drink` / `burger` / `toilet` / `map` / `umbrella` | wants one now | put that stall or a restroom / info kiosk where those guests are |
| `running_out` / `cant_afford_ride` | low on cash | ATMs: `find_installed_objects_tool("atm")` + `load_object_tool` if the scenario has none, then `place_stall`; or cut prices |
| `path_disgusting` / `bad_litter` / `sick` | dirt and vomit | zoned handymen (about 1 per 25 path tiles once intense coasters run), bins, benches |
| `vandalism` | broken benches/bins | security guards (about 1 per 200 guests), `repair_vandalism_tool` |
| `bad_value` (by ride) | price too high for the ride's age/rating | lower that ride's price; old flat rides need cheaper tickets |
| `not_while_raining` | rain | info kiosks sell umbrellas; it passes |
| `more_thrilling` | wants intensity | a new intense coaster (see Freeform) |

**Guest-count objectives** ("N guests by year Y", won on Dynamite Dunes from a one-coaster start)
- Years have **8 months** (March to October), so "year 3" is only 24 months. The game checks the
  objective once, when 24 months have elapsed (`scenarioTicks` = 24 x 16384), not when the count is first
  reached. Needs the guest count and rating >= 600 at that moment; `scenario_progress_tool` then says
  `completed`
- **The binding limit is `park.suggestedGuestMaximum`**, not marketing. Over it, arrivals drop to a
  quarter. Read it with `bridge_payload(game, "park.suggestedGuestMaximum")` (and
  `park.guestGenerationProbability`; both are recomputed every 512 ticks). The cap is the sum of each
  open ride's bonus: Launched Freefall 65, Top Spin / Go-Karts 55, Merry-Go-Round / Ferris Wheel /
  3D Cinema / Motion Simulator 45, Twist / Maze / Spiral Slide 40, Bumper Cars / Pirate Ships 35,
  Space Rings 30, Haunted House 22, wooden coaster 105, food / drink / shop / info kiosk 15, restroom 5.
  Flat rides cost about $10 of build price per cap point (Bumper Cars $440, Twist $405, Top Spin $650)
- **Arrivals per week** = 4096 x probability / 65536: base probability is 50 + (rating - 200), capped
  at 700 (about 44 a week). Campaigns add on top and **ignore the cap**: free-ride vouchers 300 (19 a
  week for $50/week), free entry 400 (25 a week, but only if the entry fee is $4+), half-price entry 200
  (needs $6+), free food/drink 200, `PARK` 250 ($350/week), `RIDE` 200 ($200/week). Campaigns are paid
  up front
- **Any loaded ride can be built regardless of research**, including "uninvented" ones (Ferris wheel,
  bumper cars, Top Spin were placed with research funding NONE). Set research to NONE and spend the
  money on rides. `place_ride_at_best_tile_tool` needs a 5x5 open site (6x6 for stalls, so use
  `place_stall`); once land is used up place rides with `place_flat_ride` (tile = centre of the 3x3
  footprint, doors on the column beside it facing a path) and check `list_ride_locations_tool(only_issues=true)`
- One 4-week `PARK` campaign early took guests from 130 to 230 in two weeks. After that natural
  arrivals (about 43 a week at rating 900+) kept growing the park; the cap, not arrivals, stopped it
- Equilibrium was about 860-960 guests on 220 path tiles. At that density "crowded" reached 60-85%
  of guests and happiness slid 224 -> 175, but rating stayed above 880. Crowding is not an objective
  risk; breakdowns are (below)
- **Rides wear out**: reliability fell to 52-70% after 10-20 months and nearly every ride broke down
  repeatedly. Hire mechanics (8 for 32 rides), then renew with `refurbish_ride_tool`. Do it in
  **batches**: `close_ride` 5-6 rides, `advance_time(4096)` once so guests clear, then
  `refurbish_ride_tool(id, close_first=false, wait_for_empty=false, open_after=true)` on each. The
  default one-ride-at-a-time mode burns half a month of game time per ride. Flat rides cost about
  $120-$200 each, a big coaster about $4,200. Doing all ~16 at once dipped rating to 794 and
  unhappy guests to 180 for about a month, so do 6 at a time
- `ENTERTAINER` hires need a loaded costume object (`rct2.peep_animations.entertainer_panda`); the
  hire tool now loads one itself
- Prices: guests judged a $1 merry-go-round (excitement ~1) as `bad_value`; $0.50 cleared it. The
  coaster at $4 stayed `good_value` and earned most of the income
- Handymen: 11 for ~220 path tiles and 900 guests kept `path_disgusting` away; give each a patrol
  box (`set_staff_patrol_tool`) over a different stretch

**Bumbly Beach (750 guests by year 2, won with 1,532 guests and rating 953 from $5,000 cash)**
- Max the loan, research NONE, then 12 flat rides along two new avenues in the first hour of game
  time: cap 210 -> 860 in a week. 750 guests arrived by month 2; the park peaked at 1,600
- **Charge park entry when `unlockAllPrices` is on** (`park.flags`): $15 entry plus the free-entry and
  half-price campaigns ($50/week each) roughly doubled arrivals and paid for everything (cash $5k ->
  $50k). Guests are not deterred while the fee stays below `park.totalRideValueForMoney`
- **Price rule for guests who paid entry**: they refuse a ride (`bad_value`) when price >
  (`ride.value` // 4) x 2. Ride value halves as the ride ages and drops 25% when another ride of the
  same type is open, so prices that were fine go bad months later. A single month of stale prices
  (plus rain) cut rating 999 -> 816 and happiness 178 -> 118. Reprice every game week, not monthly
- **Half of guests want intensity >= 4** (they think `more_thrilling` at gentle rides). Top Spin (4.8),
  Launched Freefall and the Swinging Inverter Ship serve them cheaply; gentle rides serve the rest
- `refurbish_ride_tool` resets age, so value and the price guests accept recover: Top Spins
  $203, Big Dipper $3,187

**Fast-forwarding a long run**
- `advance_time(16384)` is one game month and took about 55 s wall clock; 24 months is about 20 minutes
- Run a read-only background poller while the clock moves (park stats and
  `guest_thought_summary` every 10 s into a log file), then read a compact summary after each
  month. It costs no model tokens and catches mid-month dips. A second client on the bridge port works
  alongside the MCP server, but **connect with a raw `pyrct2.connection.Connection`, not
  `SESSION.game`**: SESSION pauses the game on connect, which strands a running `advance_time`
  (the next one then fails with `already_in_progress` until the game is unpaused)
- Delegate read-only analysis (crowd-relief path plans) to a subagent while the clock runs; tell it
  explicitly not to build or advance time. Avoid `list_rides` and `park_health_report_tool` for
  routine checks (huge output); use `get_ride`, `list_refurbish_candidates_tool` or a small script

**Other levers**
- Research greys out a category when nothing is left to research in it. `load_object_tool` adds
  any installed object (ATMs, other rides) regardless of research
- Price before paying: `buy_land_tool(dry_run=true)` (land can be about $30 a tile),
  `terraform_region_tool(dry_run=true)`, `coaster_fit_design_tool(dry_run=true)`
- Marketing campaigns bring guests fast; add staff before the extra dirt arrives
- Money is in units of 10 in raw results (`spent: 1200` = $120); tools that report dollars say so

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

On failure, report `best_partial`, `ring_unowned`, screenshot via `capture_game_view`, and suggest `buy_land_tool` if unowned ring tiles blocked the build.

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
8. z accounting: tile_z units (`baseZ // 8`); Up25 = +2/piece, FlatToUp25/Up25ToFlat = +1
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

- `kind_rows` legend: `.` clear flat owned, `X` unowned (**never crossable**), `~`/`T`/`P`/`s` flyable when track base z > that tile's top z (`obstacle_tops.rise_rows`: per-tile height above ground in base 36, `^` = see `overflow`), `/` owned slope (supports OK)
- Keep the **station + entrance/exit on clear ground** tiles; elevate the rest of the circuit over paths/water/rides
- A turn template that closes: station x4, climb `[6, 4..., 9]`, turns `42` x4 with leg flats balanced (x: legA + 2*k_up + 9 = legC), descend `[12, 10..., 15]` into `EndStation`
- If nothing fits, say so and offer: terraform owned slopes, demolish a specific ride (ask first), or load a roomier save

## Safety rules

- **Never** call `deleteAllRides` — it demolishes the entire park
- Only delete agent-created shells: `coaster_cleanup_session_rides` or `coaster_delete(ride_id, confirm_destructive=true)` on a known `ride_id`
- Never demolish existing park rides without explicit user approval
- Prefer `refurbish_ride_tool` for old, unreliable, or breakdown-stuck rides before demolishing or cutting prices
- Use `list_refurbish_candidates_tool` to rank targets by **downtime and reliability** (same metrics as the ride Maintenance tab)
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

## Freeform coasters (preferred for anything bigger than a hairpin)

- **Find a station first:** `coaster_find_freeform_sites_tool()` sweeps every possible station
  spot in the park (or `area=`) in seconds and ranks the ones that close a circuit. In a full
  park very few do; if none close, try `lifts="16,13,10"`, `avoid_tight_turns=false`, a smaller
  `budget`, or buy land (price it with `buy_land_tool(dry_run=true)`)
- `coaster_generate_freeform_tool(x1, y1, x2, y2, station_x, station_y, station_direction, lift, budget)`
  wanders modules (sloped turns, drops, hops, helixes, banked turns, mid-course lifts, loops
  and corkscrews on looping types) inside the owned rectangle, flies over paths/rides only
  above their top, and closes back into the station with A*. It returns `estimated_cost`
  (the game's own price for the track) and `park_cash`. Place the result with
  `coaster_fit_design_tool` (add `excavate=true` if it tunnels; `dry_run=true` re-prices)
- Defaults that encode hard lessons:
  - `avoid_tight_turns=true`: a 31-high drop straight into 3-tile banked turns rated intensity
    14.8, which guests refuse (they ride up to about 10)
  - `auto_chain=true`: chain lifts go on climbs the train probably can't coast up
    (`auto_chained_pieces`); a 112-piece ride stalled without them
  - `clear_small_scenery=true`: track removes trees and flower beds as it is placed
- Built-up parks need cruising height: larger `lift` plus mid-course lifts let the track pass
  over existing rides; a ground-level first drop traps it among obstacles
- Freeform circuits often enclose their own station; plan guest access (a bridge, tunnel, or
  the one open side) before placing. A station above the ground needs a raised landing and a
  ramp; the land beside it may not be owned
- Theme with `theme_ride_tool` (name, colours by name, entrance style e.g. "Log Cabin")

## Running a coaster after it is built

- **Verify guests can ride**: within a few in-game days `get_ride` should show customers.
  0 customers with guests standing in the queue means the queue is not linked to the entrance.
  `get_ride_location_tool(id)` lists doors without a path
- **Ratings that never settle** after a test mean a stalling train: `get_ride_trains_tool(id)`
  shows where (`rolling backwards` on a climb). Add chains to the climbs and flats just before
  that spot with `coaster_set_chain_lift_tool(id, '[[x,y],...]', confirm_destructive=true)`,
  then `coaster_test`
- **Ratings move after changes**: the build test can report a partial lap; trust the rating
  after a few laps in operation. Any setting change re-rates the ride
- **Ride settings**: `ride_setting_tool(id, "lift_hill_speed")` shows the allowed values for that
  ride before you change anything (lift speed was 4-6 on a looping coaster, not 10+)
- **Too intense** (above about 10): a demolish of a never-opened ride refunds about 99% of the
  track, so rebuilding a better layout is cheap; brakes need flat track before the hot spot
- **Entrance facing**: a ride entrance/exit's stored direction points at the station tile
  (0 = -x, 1 = +y, 2 = +x, 3 = -y); its opening is direction + 2. A backwards entrance
  leaves the queue's end tile without an edge into it. The fit tool now re-points these
- **Queues**: lay the queue starting at the entrance's front tile; if the end tile still
  lacks the entrance edge, remove and re-place that one tile. Keep a 1-tile buffer between a
  queue and a parallel path (queues join adjacent paths sideways)
- **Entrance side**: put entrance and exit on the station side that faces open ground. A
  lane between the station leg and the return leg needs a path tunnelled under high track
- **More trains**: block-sectioned mode (34) allows stations + block sections - 1 trains, and
  a chain lift top counts as one block. To add a train, swap the flat before the station for
  Block Brakes with `replace_track_piece_tool(..., new_track_type=216, confirm_destructive=true)`,
  then set trains and reopen. `set_num_trains_tool` reports the trains actually running
- `openrct2_status` warns when the deployed ride-builder plugin differs from the repo copy;
  a stale plugin silently runs old (buggy) code

## Non-coaster goals

| User request | Tool sequence |
|--------------|---------------|
| Make it cuter | `get_path_graph_tool` → `apply_theme_preset_tool("cute")` → `capture_game_view` |
| Staff on paths | `get_path_graph_tool` → `optimize_staff_coverage_tool` → `park_health_report_tool` |
| Tune one ride | `get_ride` → `set_ride_price` / `ride_setting_tool` |
| Fix complaints | `guest_thought_summary_tool` → table in "Running a park" → `advance_time` → re-check |

## Footpaths and path shapes

- **Ask filled or hollow** when a shape request is ambiguous: "a square path area" can mean a solid plaza or an outline (one user meant the outline)
- **Gap repair**: `manage_paths` `place_line` / `place_tile` fill one-tile gaps beside the tiles just placed. Pass `repair_gaps=false` for exact shapes (outlines, lettering) and when building next to a shape whose 1-tile gaps are intentional. Paths the user draws in the game UI are never touched
- `repair_path_connectivity_tool` fills gaps **park-wide**, including inside outlines and lettering; run it with `dry_run=true` first
- `find_open_land_tool` returns level, owned sites with no paths, track, entrances or trees (`allow_scenery=true` accepts tree-covered land). `min_width` is the x extent, `min_height` the y extent
- **Surfaces**: `manage_paths(action="list_surfaces")` lists the loaded footpath surfaces; pass `surface=` (identifier or name, e.g. `"red and brown tiled"`) to `place_line` / `place_tile`. Gap fills use the same surface
- **Decorative paths** (lettering, path art) should stay at least 1 tile from guest paths so they don't join the network. They then show as "unreachable" in connectivity reports, which is expected
- **Drawing text**: screen direction depends on camera rotation, so work out which map axes run right and up from a screenshot of known path tiles before placing. Preview the bitmap first, then verify with `inspect_area_at_tile`. 3-wide glyphs with a 5-tile x-height and 1-tile letter gaps read well (about 12 tiles per letter)

## Vision

- `capture_game_view` screenshots the OpenRCT2 window (Windows and macOS; not available for headless games)
- Windows: works when the window is covered and never takes focus; a minimized (e.g. fullscreen, unfocused) game needs `bring_to_front=true`, which un-minimizes it just for the capture
- macOS: grant **Screen Recording** to the terminal running Claude Code
- Use `inspect_area_at_tile` for ASCII path grids + optional screenshot
- `focus_camera_tool(tile_x, tile_y, zoom, rotation)` centres the view and captures; `crop=0.4` keeps native resolution on 4K screens

## Mapping

- **Look before building:** `render_map_tool()` shows the whole park top-down (+x right, +y down, tile numbers on the edges), independent of the game camera. Use it instead of screenshots for planning; use `focus_camera_tool` to check how a build looks in the game
- **Preview plans:** pass `preview=true` to `landscape_tool`, `build_maze_tool`, `manage_paths` (line/ramp/tile) and `coaster_generate_freeform_tool`, or draw anything with `render_map_tool(overlay_json=...)`
- **Ride questions:** `get_ride_location_tool(id)` / `list_ride_locations_tool(only_issues=true)` give footprints, doors (with the guest-side path), queues and problems; `render_map_tool(highlight_ride=id)` shows them
- **Checkpoint around big edits:** `map_checkpoint_tool(name, area=...)`, build, then `map_diff_tool(name, image=true)` for exactly what changed and what it cost. Terraform, clear-area and 3D path builds report `changes` automatically
- **Name places:** `define_area_tool("East Gardens", ...)`, then pass `area=` to render, landscape, terraform, clear-area, checkpoint and region tools. `suggest_areas_tool` proposes names
- **Units:** heights are tile_z everywhere (1 land step = 2 tile_z). Directions: 0 = -x, 1 = +y, 2 = +x, 3 = -y. ASCII grids print lowest y first
- `get_recent_actions_tool` shows what the player just did in the game

## Landscaping and mazes

- Flower beds: small scenery `rct2.scenery_small.tg1`-`tg14` (full tile) and `tg15`-`tg21` (quarter clumps). All are named "Gardens", so pick by identifier; `list_scenery_objects_tool(search="garden")`
- `landscape_tool` modes: `borders` (stripes along paths), `terraces` (one colour per height), `lawns` (tree grid + centrepiece). Always run `dry_run=true` first and pass `budget`; it skips queues and ride entrances
- `build_maze_tool`: entrance/exit tiles must sit just outside the maze rectangle, beside a maze tile
- Terraforming is expensive (a small stepped hill cost $8,530) and cost grows faster than height. Run `terraform_region_tool(dry_run=true)` first; heights are tile_z

## Connection troubleshooting

- `openrct2_status` reports `connected: false` with the reason; the server reconnects automatically after the game restarts
- `park_loaded: false` / `game_mode: "title"`: no park is open (the title screen's demo park answers queries). Ask the user to load a park before any live work
- Run only one OpenRCT2 instance at a time (both would claim the same plugin ports)
- If the server itself is down, reconnect it with `/mcp` in Claude Code
