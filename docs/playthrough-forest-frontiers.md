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

## Phase 3: 3D paths and bolder coasters

- **Paths in 3D.** `manage_paths` only placed flat ground paths, so bridges and tunnels
  were impossible through MCP. Added `path_build.py` (raw `footpathplace`): flat paths at
  any tile_z, slope tiles (one land step = 2 tile_z per tile), `plan_ramp` for straight
  ramps, and new `manage_paths` options (`height`, `slope`, `end_height`, `place_ramp`,
  `excavate`).
- **Bridges work as-is**: a humpback footbridge climbing to +6 tile_z and back placed all 11
  tiles first try, on wooden supports.
- **Tunnels need a cutting.** Fully underground tiles (path z <= ground - 6) place fine,
  but ramp tiles that pass through the surface fail with "Raise or lower land first".
  Lowering just those tiles' land to the path base (`landsetheight`) and retrying makes a
  cutting that opens into the tunnel. `excavate=true` automates this.
- pyrct2's `paths.remove` only removes ground-level paths; elevated tiles needed raw
  `footpathremove` with their z (still a gap in `manage_paths remove_*`).
- **Steep chain lifts are not allowed** on the Looping Roller Coaster: Up25ToUp60 with chain
  failed at probe with the uninformative "Failed to place track piece: 2". 60-degree
  *drops* are fine (12, 13, 11, 11, 14, 15 = -26 in 6 tiles).
- **Design search beats hand design.** Brute force over loop hand, corkscrew pair, U-turn
  style, hill count, straight length and origin, filtered by lint + an owned-tile mask, finds
  the few layouts that fit. The 8x23 north-west pocket fit nothing with a 25-degree lift;
  buying the for-sale east strip (x 63-73, 252 tiles, $7,050, about $28/tile) did.
  The survey missed that a corner (x 63-66, y 26-30) was unowned too; the probe found it.
- **Thunder Ridge** (Looping RC, 68 pieces, 7x36): +26 chain lift, 60-degree drop, vertical
  loop, banked turnaround, corkscrew pair, three airtime hills. Excitement 5.30, intensity
  8.80, 55 riders in its first weeks despite the intensity. Best coaster in the park.
- **Entrance logic, live:** the new open-side preference moved the exit out of the inner lane
  automatically. It placed entrance and exit on adjacent tiles, though; spread them to the
  station's ends so the queue and exit path don't meet end to end.
- **Observation Tower is a tracked ride, not a flat ride.** `place_flat_ride` and the
  auto-placer fail ("no footprint dimensions"). Built it with raw actions: `ridecreate`
  (type 14), Tower Base (track 66, a 3x3 platform with clearance 96) then Tower Sections
  (track 67, 32 high each) stacked from the base's clearance top. 16 sections, excitement
  3.66. Its entrance/exit open onto the end of the tunnel path.
- Park at Y2 May: 618 guests, rating 891, 22 rides.
- **Underground coaster track works.** "Mine Plunge" (Classic Mini RC, 30 pieces) leaves its
  station at the foot of the south-east hill, dives 8 tile_z below the station into a
  tunnel, U-turns underground and chain-lifts back out. 28 of its tiles are underground.
  A terrain-aware search scored each layout tile as open air / tunnel / needs a cut, using
  the live height map (surface baseZ + 2 on sloped tiles, train clearance 3 tile_z), and
  picked the layout with the most tunnel and fewest cuts.
- **Cuttings for track work like they do for paths.** Flattened the sloped station tiles,
  lowered the tile where the chain lift surfaces, and the probe then placed every piece. The
  one unplanned failure ("Failed to place track piece: 9") was a surfacing tile; digging it
  fixed it. Track error codes are numbers only (2 = not allowed for this ride/piece,
  9 = terrain in the way), which the tools should translate.
- **Paths can run over buried track.** The exit path crosses directly above the tunnel
  (path z12, track z7). The entrance enclosure check still flagged the exit as "enclosed"
  because it treats all low track as a wall; track below ground isn't one.
- Excitement for the mostly flat, dark tunnel ride is low (1.88); it still drew 46 riders
  in its first two weeks. Dips and turns underground would raise it.
- Land: bought the hill site and a corridor (about 250 tiles, $6.9k) and extended the red
  promenade east along y=66 to reach it. The queue runs down x=70, the exit path down x=75.
- Park at Y2 June: 813 guests, rating 842, 24 rides, about $4.1k/month net.

### MCP opportunities from phase 3

- `coaster_fit_design_tool` could dig automatically: when a piece fails with the terrain
  code and the track is below ground, lower that tile and retry (as `excavate` does for
  paths), and report the cuts.
- A terrain-aware design scorer (open air / tunnel / cut per tile) belongs in the linter
  when an envelope is given; it would replace the ad-hoc search script.
- Translate track placement error codes into messages (2, 9, ...).
- Enclosure check: ignore track whose top is below the surface (buried) and keep treating
  high track as walk-under.
- Tower rides (Observation Tower) need a builder: `ridecreate` + Tower Base (66) + Tower
  Sections (67) stacked from the base's clearance top; flat-ride tools can't place them.
- Park messages only expose an archive that stops at the last save; there are no current
  (unarchived) items and no year, so alerts can be stale. Cross-check alerts against live
  ride state (customers, connectivity) before recommending fixes.
- `coaster_site_envelope_tool` still pretty-prints `ground_z_rle_rows` one number per line.

## Phase 4: skywalk, suspended coaster, park upkeep

- **3D paths through MCP work.** A skywalk built with three `manage_paths` calls
  (`place_ramp` up 12 -> 20, `place_line height=20`, `place_ramp` down) passes 13 tile_z under
  Thunder Ridge's lift and 3 above its return track, opening up the east strip. The
  landing stalls on it sold 130+ items in a few weeks, so guests use it.
- End a ramp on a flat tile: a stall or entrance beside the last *sloped* ramp tile is risky.
  `place_ramp` to one tile past the descent gives a flat landing. Height-aware removal took
  out a deck at z20 and slopes at 14-18 in one `remove_line`.
- **`place_line` follows terrain.** On sloped land it placed tiles at z12/14/16 that don't join
  (edges 0), and the connectivity report still called everything reachable: path
  connectivity is height-blind. Same blindness: the auto-placer routed an entrance path
  into the tunnel at z6 from a ground tile at z12 and reported success, and gap detection
  flags (48,46) between a ground path and the tunnel below. Fix: compare base z (and slope
  ends) when linking tiles; flatten or use `height=` on slopes.
- `place_ride_at_best_tile_tool` ignores near_x/near_y when no site fits there and silently
  places the ride across the park (Space Rings went to the NW pocket instead of the east strip).
- **Suspended Swinging Coaster "Canopy Glider"** (ride type 2): +14 chain lift, 25-degree drop,
  three swooping hills, 5x25 footprint along the east edge. Excitement 2.56. No 60-degree or
  banked pieces used. Its station's outer side was on the hill's foot; entrances need flat
  land, so placement there failed with "Raise or lower land first" and the tool fell back
  to the enclosed inner side. Flattened x 75-76 and placed both on the outer side. The
  open-side check should require flat land at station height.
- Entrance and exit on the same outer side: queue from the entrance runs back to meet the
  exit path, which feeds the network. Works well when only one side is open.
- **Upkeep:** guest thoughts (now readable) were full of "path disgusting", litter, vandalism
  and "sick" at 878 guests with 3 handymen; rating slid 891 -> 799 -> 781.
  `get_complaint_hotspots_tool` reported 0 hotspots despite those thoughts. Hired 4 handymen
  and 2 security guards, repaired 7 vandalized benches/bins. `optimize_staff_coverage_tool`
  only zones staff it hires itself.
- Park at Y2 August: 920 guests, 27 rides/stalls.
- **Rating crash and recovery.** Rating fell 891 -> 588 -> **250** in Y2 (guests 925 -> 817)
  with no breakdowns or crashes. A full guest scan (834 guests) showed why: 373
  "path disgusting", 319 "bad litter", 107 "vandalism", 153 "crowded", 77 "sick", 204 guests
  below happiness 64. High-nausea coasters (Thunder Ridge nausea 4.56) made vomit faster than
  7 unzoned handymen cleaned. Fix: 13 handymen with patrol zones over the main path, coaster
  exits and promenade; 4 security guards; 55 bins/benches added or repaired; a parallel
  bypass path (x=54) to split the single main trunk; restroom on the bypass. Rating
  recovered 250 -> 516 (2 weeks) -> 810 (6 weeks), avg happiness 141 -> 186.
- Rule of thumb from this park: about 1 handyman per 25 path tiles once nauseating coasters
  are open, zoned, not roaming; 1 security guard per ~200 guests. Watch the thought mix, not
  the rating: the rating lags dirt by weeks and falls off a cliff.
- Tooling gaps found: no litter/vomit count query; `get_complaint_hotspots_tool` returned 0
  while hundreds of guests complained; aggregating thoughts over all guests (one
  `guests.list()` call, ~1s for 800 guests) was the useful signal. A `guest_thought_summary`
  tool should do exactly that.

## Phase 5: new tools live (tower builder, auto-dig, pedestrian tunnel)

- `build_tower_ride_tool` built a 20-section Observation Tower in one call (dry run first
  with `confirm_cost=false` showed the plan). Excitement 4.03, the highest tower yet.
- `coaster_fit_design_tool(excavate=true)` placed "Valley Dip" (Classic Mini RC) on hilly
  bought land with no manual terraforming: it dug 15 tiles, ran out of its 12 retry rounds at
  piece 26, and a second call dug the last 4 and placed all 30 pieces. Each round digs one
  failing piece, so terrain-heavy layouts need many rounds. Better: pre-dig every
  intersecting tile from the simulated footprint before the first probe.
- Readable track errors work: "status 9: terrain or scenery in the way (track crosses the
  land surface; lower the land or move the piece)".
- **Pedestrian tunnel under a coaster**: the promenade now ramps down at x 83-85, runs at
  tile_z 6 under Valley Dip's return track (track z10) and under its station (z12), and
  ramps back up at x 93-95. Both tunnel mouths were dug automatically by `excavate`.
  Guests use it: 70 riders reached the ride through it in two weeks.
- Small gaps: `place_line` with `height` refuses a 1-tile line ("Line needs at least two
  tiles"); a queue tile placed on a bowl's sloped edge took the slope's lower height and
  missed the link, so set `height=` explicitly on slopes.
- **Marketing works**: a 4-week PARK campaign took guests 826 -> 920 in a month (and 1,016
  a month later). More guests brought the dirt back (rating 809 -> 712) until 5 more zoned
  handymen (18 total for ~1,000 guests and ~350 path tiles) recovered it to 819.
- **Rain**: 333 guests thought "not while raining"; added two Information Kiosks (umbrellas)
  on the bypass and the promenade.
- **Bad value by ride**: tallying `bad_value` thoughts by item pointed at three aging rides
  (Spiral Slide 53, Haunted House 1 38, Ferris Wheel 26) with zero good-value thoughts;
  cut their prices. Ride value decays with age, so old flat rides need cheaper tickets.
- Park at Y3 June: 1,016 guests, rating 819, avg happiness 195, 31 rides/stalls.
- **New intel tools, live** (run as scripts before the reconnect): the height-aware
  connectivity agreed with the game on all 344 path elements including bridges and tunnels,
  and the false gaps beside the tunnel disappeared. `guest_thought_summary` gave averages,
  top thoughts, per-ride counts and concrete recommendations in one call (1,002 guests).
  The thought-hotspot cells pinpointed dirt at the skywalk landing (x 72-79, y 40-47) and the
  wooden coaster exit corridor (x 56-63); one zoned handyman each cleared both. Bug fixed on
  the spot: guests riding a ride report x = -32768 and formed a bogus cell.
- **Crowding** became the top complaint (305 -> 413 guests) as the park grew: almost all
  east-side traffic squeezes through the x=62 corridor. Added a parallel lane on x=61 and a
  second lane along y=28. Crowding thoughts still rise with guest count, but they did not
  drag the rating.
- Park at Y3 August: **rating 925** (best so far), 1,116 guests, avg happiness 212.6,
  3 unhappy guests, $24k cash, 21 handymen, 6 security, 3 mechanics.

## Phase 6: freeform coaster generator

- **Why every coaster looked the same**: all of them came from my own brute-force search over
  one recipe (station, lift, drop, straight leg, U-turn, straight leg back, U-turn), which
  always yields a long narrow hairpin. That recipe made closing the circuit trivial; the
  piece vocabulary was never the limit (it has sloped quarter turns 34-37/46-49, quarter and
  half helixes 102-109/87-94, banked turns, half loops, large corkscrews, quarter loops).
- **New `coaster_freeform.py` + `coaster_generate_freeform_tool`**: random walk over flat-to-flat
  *modules* (sloped turns, drops, hops, camelbacks, helixes, banked turns, loops/corkscrews on
  looping types) inside an owned-tile mask, crossing itself only with a 5 tile_z gap, flying
  over paths/rides only above their top, tunnelling only when deep enough; an energy model
  (head = lift peak - height - 0.12/piece) gates unchained climbs and inversions; then an A*
  search over closing modules (turns, dips, short chain lifts) steers the track back into its
  own station. Candidates are linted, scored (length, drops, turns, inversions, footprint) and
  the best is returned with a height map.
- On open ground it produces 60+ piece layouts covering ~145 tiles with a dozen direction
  changes. In the crowded park only one station/lift combination of 1,008 tried could close:
  "Sky Sweeper" (Looping RC, 46 pieces, 20x20 footprint, turning first drop, camelback,
  mid-course chain lift, staircase of alternating turns flying over the park). Excitement
  4.90 with a +14 lift and no inversions; the shape alone earns rating.
- Generator lessons: stations must be checked against the mask (two unowned tiles killed
  most candidates); the first drop needs turning variants or it runs into whatever is
  ahead; a drop to ground level traps the train among obstacles, so cruising height and
  mid-course lifts matter in built-up parks. The circuit enclosed its own station, so guest
  access came from the one open side (bought two tiles and ran a path to the main entrance).
- Park at Y3 October: **rating 961**, 1,243 guests.
