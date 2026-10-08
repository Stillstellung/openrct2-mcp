# Forest Frontiers playthrough log

Agent-driven playthrough used to learn scenario strategy and find MCP server gaps.

## Scenario

- Goal: 250 guests and park rating >= 600 by end of October, Year 1 (game starts March 1).
- Start: $10,000 cash, $10,000 loan (max $30,000), park closed, no rides.
- Map 128x128. Park entrance at (50..52, 19); entry path runs north to the map edge
  (47,1) and south to (51,30). Large flat owned clearing south of it at tile_z 12,
  roughly x 43..66, y 30..65+. Forest everywhere else.
- Available at start: Wooden RC, Ladybird (junior) coaster, Twist, Pirate Ship,
  Haunted House, Spiral Slide, Merry-Go-Round, Car Ride (3 vehicles), Rowing Boats,
  Steam Train; Restroom, Drinks, Burger Bar, Fruity Ices.

## Strategy (sources: StrategyWiki, Skeg's scenario guide)

- Pause, set research to shops first (Information Kiosk -> umbrellas, maps), then thrill, then gentle.
- Build a cheap mix: a few gentle + thrill flat rides, one of each stall type, restroom.
- Modest first coaster; don't overbuild.
- Open the park early. Entrance fee around $5 at the start (OpenRCT2 may be in
  pay-per-ride mode; check).
- Hire handymen early, mechanics roughly one per few rides.
- 250 guests is mostly about guest generation: park rating, number of rides, marketing.

## Learnings (in-game)

- Timing: `advance_time` 4000 ticks is about 7 in-game days, so a month is about
  17,500 ticks. The goal deadline (end of October Y1) is about 140k ticks from the start.
- This copy of the scenario is pay-per-ride with free entry (entrance_fee 0, rides priced).
- Opening build (day 1, about $7k spent): straight main path south from the entrance,
  Merry-Go-Round, Twist, Haunted House, Pirate Ship, Spiral Slide on both sides with
  entrance+exit facing the path, then Burger, Drinks, Restroom, Fruity Ices pairs facing
  the path. 1 handyman + 1 mechanic. Rating 700 after one week.
- Flat-ride footprints via `place_flat_ride`: 3x3 rides are centered on tile_x/tile_y;
  the 2x2 Spiral Slide covers (x..x+1, y-1..y); the Pirate Ship with direction NORTH is
  1x5 along y. A bad entrance tile returns the list of valid adjacent tiles, which is a
  quick way to learn a footprint.
- Coaster: custom out-and-back for the Classic Wooden RC (ride type 99, object 1):
  5 station pieces, +14 chained lift, -14 drop, one +4 hill, U-turn, six +2 bunny hops,
  U-turn. Footprint 5x34. Lint closure search took a 10-line script (vary flat counts).
  Result: excitement 2.72, intensity 6.97, about $4k. Saved as template `timber-rattler`.
  Bunny-hop chains push intensity up fast; use fewer, gentler hops next time.
- Entrance pitfall: auto entrance/exit put the coaster entrance in the 3-tile lane between
  the station leg and the return leg. Fixed by running the queue down the lane and out
  under the lift hill (track 9+ tile_z above the path), then a path loop back to the main
  path. Plan station sides so one faces open ground.
- Trees block ground-level track; `clear_area_tool` over the footprint first (cheap).
- Guests: 16 (day 8), 92 (Mar 27), 187 (Apr 14), 254 (Apr 29). Goal guest count hit in
  2 months with 6 rides + 1 coaster. Income is the bottleneck, not guests: cash stayed
  around $3k because ride prices were defaults ($1-1.50).
- Pricing: with free entry, the coaster logged 142 "good value" thoughts and 0 bad at $3;
  raised to $4.50 with no complaints. Cheap flat rides went to $1.50-2.
- Staff orders bug: staff hired through `manage_staff` came with orders 0, so mechanics
  never fixed rides (Twist sat broken 2+ weeks with 2 mechanics nearby) and handymen
  never swept. Set orders 3 (mechanic) / 7 (handyman). Always check `orders` in
  `manage_staff list` after hiring.
- 389 guests on Jul 1 with rating ~800; cash still only $3-4k. Running costs
  (4 staff + 14 rides) eat most of the ride income at these prices.
- Result: **scenario completed** at the end of October Y1 with 456+ guests, rating 809,
  company value $43,615. Total build: 6 flat rides, 1 wooden coaster, 9 stalls, 4 staff,
  no marketing, no loan increase. Guest goal was met by late April (month 2).
- Takeaway: Forest Frontiers is easy for an agent if the first day's build is right.
  The real risks were tool-level (idle staff, an entrance trapped inside the coaster),
  not strategy.
- Year 2: added a family coaster (Classic Mini RC, ride type 95, object 3): +6 lift,
  -6 drop, two right U-turns, one +2 hop, 20x5 footprint. Excitement 3.67, intensity
  4.72, higher excitement than the wooden coaster at a third of the size. Saved as
  `ladybird-loop`. Its exit again landed inside the circuit; the exit path runs under
  the lift where track base is 5 tile_z above the path (5 was enough clearance).

## Phase 2 (after `/mcp` reload with the fixes)

- Fixed tools verified live: compact `list_rides`, `get_map_region` row strings,
  `manage_paths` connectivity summary (park gate detected, 1 component), the
  `inspect_area_at_tile` map with T/R/E/X markers (made the queue layout readable),
  `set_loan_tool`, `coaster_fit_design_tool` placement summary, staff hire orders 3/7.
- **Backwards entrances (big one):** the deployed `ride-builder.js` predated a source
  fix, so coasters whose station runs east-west got entrance/exit facing *into* the
  station (stored direction 3 at y-1, should be 1). Queue end tiles never linked
  (edges lacked the entrance bit), so the Ladybird coaster had 0 riders for months.
  The game had even posted "Guests can't get to the entrance" in Y1; no tool surfaced it.
  Fixed in-game with raw `rideentranceexitremove` / `rideentranceexitplace`, then
  re-laid the queue end tile. Rule: entrance stored direction points at the station
  tile; its opening is direction + 2. Added `_fix_entrance_facing` in design_library.
- Queue laying order matters only for linking: re-placing the end tile after the
  entrance is correct makes the game add the entrance edge.
- Second train: block-sectioned mode alone kept 1 train (max trains = stations +
  block sections - 1, and the lift top is the only block). Swapped the flat before the
  station for Block Brakes (track 216) via raw `trackplace` (pyrct2's enum rejects ride
  type 99), then 2 trains ran 10+ days with no crash.
- Looping coaster "Forest Loop" (ride type 15): +18 chain lift, 60-degree drop
  (12,13,11,14,15 = -18 in 5 tiles), vertical loop (6,40,15), banked U-turn, two hops.
  31x5, excitement 4.82, the best ride in the park. Corkscrew variants needed 8 rows,
  more than the owned strip allowed. A search script over loop/cork/U-turn combinations
  + ownership mask found the fit in seconds.
- Decorating: `apply_theme_preset_tool("cute")` only adds benches/bins/terrain paint
  here (no flower objects loaded). Hand-placed 23 topiary/bush/fountain/statue objects
  along the promenade with `place_small_scenery`.
- Park at Y2 April: 545 guests, rating 837, 19 rides, $12k cash, $20k loan.

### More MCP improvement opportunities (phase 2)

All of these were fixed after phase 2 (see the commit that follows this log):

- Deploy check: the server should compare the deployed plugin file with the repo copy
  (hash) and warn in `openrct2_status`; a stale plugin silently broke entrances.
- `set_num_trains_tool` / `set_cars_per_train_tool` report the requested value, not what
  the game applied (it clamped trains to 1). Read back and report the real count and
  max trains.
- No tool for swapping one track piece (e.g. add block brakes) and pyrct2's `RideType`
  enum lacks newer ride types (99), so typed actions fail; use raw execute there.
- `park_health_report_tool.recent_messages` shows the oldest messages, not the newest,
  and doesn't flag "can't get to the entrance/exit" alerts.
- `sample_guests_near_tile_tool` serializes thoughts as `{}`.
- `place_ride_at_best_tile_tool` picked an exit whose front tile was inside the Ferris
  Wheel; the side picker must require a free front tile.
- `manage_paths` reports deliberate path/queue buffers as one-tile gaps; gap repair
  should ignore gaps that touch queue tiles.
- Enclosure check treats a lane as reachable when high track could be tunnelled under;
  it should prefer the side that opens onto open ground without a tunnel.
- `fill_missing_benches_and_bins_tool` tries the unowned approach road outside the gate.
- `set_research_priorities_tool` looked like it did not persist, but OpenRCT2 has no research
  order: it only enables/disables categories. The tool echoed an ordering it cannot apply;
  it now reports the enabled/disabled categories the game actually holds.
- `apply_theme_preset_tool("cute")` promises flowers but places none when no flower
  objects are loaded; fall back to shrubs/topiary.
- `coaster_site_envelope_tool` `obstacle_tops` lists every tile (very long on big radii).
- `analyze_path_connectivity_tool` still returns the full tile lists, and its
  `disconnected_components` includes the main network.

## MCP server improvement opportunities

- `get_map_region_tool` returns pretty-printed nested JSON (one number per line): a
  40x40 region with two numeric layers is ~50k chars. Return each layer as compact
  row strings (like the `ascii` layer) or RLE.
- `list_rides` was 166k chars for 9 rides: `station_queue_times` listed all 255 station
  slots per ride. Fixed (Python filter + plugin skips unused stations).
- No loan control. Added `set_loan_tool`.
- `manage_staff hire` defaulted to orders 0 (idle staff). Fixed: 0 now means defaults.
- `optimize_park_pricing_tool` holds every price when excitement is "low", even with
  142 good-value / 0 bad-value thoughts; stalls report "invalid satisfaction". It should
  raise prices when good-value thoughts dominate.
- `coaster_plan_build_tool` output was 243k chars (a raw `survey` field) and ignored
  near_x/near_y. `manage_paths` connectivity output lists every tile (10-20k chars) and
  mistakes ride entrances for the park entrance. `find_stall_sites_tool` only offers
  dead ends. `inspect_area_at_tile` ascii hides track/rides. `coaster_fit_design_tool`
  can put the entrance inside the circuit. `place_ride_at_best_tile_tool` can't resolve
  identifiers and puts entrance/exit inside 3x3 footprints. All fixed offline by a
  fix-up agent (130 tests pass); not yet exercised against the live game.
- `get_park_messages` returned nothing while `park_health_report_tool` showed breakdown
  and research news (archived messages are probably filtered out).
