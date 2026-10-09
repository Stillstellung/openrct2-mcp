"""Watch a running park from a second connection: status, a background poll log, summaries.

Connects with open_bridge(), which leaves the game running, so it can poll while
advance_time runs in Claude Code (SESSION.game pauses the game on connect).

Usage (game running with a park loaded; macOS uses .venv/bin/python):
    .venv\\Scripts\\python.exe scripts\\park_watch.py status
    .venv\\Scripts\\python.exe scripts\\park_watch.py watch [--interval 15] [--log PATH] [--reprice]
    .venv\\Scripts\\python.exe scripts\\park_watch.py summary [--log PATH]

watch appends one JSON line per poll to --log (default: park-watch.jsonl in the
system temp folder) until Ctrl+C or until the <log>.stop file it creates is deleted.
--reprice also applies the ride-value price rule on every poll (the
optimize_park_pricing_tool rule without guest sampling), so prices follow ride
values as rides age. summary prints what changed since the previous summary.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from openrct2_mcp.bridge_fast import bridge_payload
from openrct2_mcp.connection import RideBuilderClient, open_bridge
from openrct2_mcp.finance_tools import optimize_park_pricing_from_guest_feedback
from openrct2_mcp.guest_intel import guest_thought_summary

DEFAULT_LOG = Path(tempfile.gettempdir()) / "park-watch.jsonl"
# Thoughts with a fix in the park-director skill's thought table.
PROBLEM_THOUGHTS = (
    "crowded", "path_disgusting", "bad_litter", "vandalism", "sick", "very_sick", "toilet", "drink",
    "burger", "hungry", "thirsty", "map", "umbrella", "not_while_raining", "running_out",
    "cant_afford_ride", "bad_value", "queuing_ages", "more_thrilling", "intense", "tired", "lost",
)
RIDE_PROBLEMS = ("bad_value", "queuing_ages", "cant_afford_ride", "sickening", "intense")


def park_stats(game: Any) -> dict[str, Any]:
    def q(endpoint: str) -> Any:
        return bridge_payload(game, endpoint)

    return {
        "month": q("date.monthsElapsed"),
        "day": q("date.day"),
        "cash": q("park.cash") / 10,
        "loan": q("park.bankLoan") / 10,
        "rating": q("park.rating"),
        "guests": q("park.guests"),
        "cap": q("park.suggestedGuestMaximum"),
        "prob": q("park.guestGenerationProbability"),
        "status": q("scenario.status"),
    }


def poll(game: Any, ride_builder: RideBuilderClient, reprice: bool) -> dict[str, Any]:
    row: dict[str, Any] = {"t": round(time.time()), **park_stats(game)}
    summary = guest_thought_summary(game, ride_builder, top=12)
    row["avg"] = {k: round(v) for k, v in summary.get("averages", {}).items()}
    row["unhappy"] = summary.get("unhappy_count")
    row["thoughts"] = {t["type"]: t["count"] for t in summary.get("top_thoughts", [])}
    row["ride_problems"] = {
        r.get("ride_name") or f"ride {r['ride_id']}": problems
        for r in summary.get("ride_thoughts", [])
        if (problems := {k: v for k, v in r["counts"].items() if k in RIDE_PROBLEMS})
    }
    if reprice:
        result = optimize_park_pricing_from_guest_feedback(game, ride_builder, guest_feedback=False)
        row["repriced"] = [[c["name"], c["old_price"], c["new_price"]] for c in result["applied"]]
    return row


def cmd_status(_args: argparse.Namespace) -> None:
    game = open_bridge()
    stats = park_stats(game)
    stats["paused"] = game.get_status().get("payload", {}).get("paused")
    print(json.dumps(stats))


def cmd_watch(args: argparse.Namespace) -> None:
    log = Path(args.log)
    stop = log.with_name(log.name + ".stop")
    stop.touch()
    ride_builder = RideBuilderClient()
    game = None
    print(f"Polling every {args.interval:g}s into {log}; delete {stop} or press Ctrl+C to stop", flush=True)
    try:
        while stop.exists():
            try:
                if game is None:
                    game = open_bridge()
                row = poll(game, ride_builder, args.reprice)
            except Exception as exc:  # noqa: BLE001 - game restarting or busy: log it and reconnect
                row = {"t": round(time.time()), "err": str(exc)[:300]}
                game = None
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        stop.unlink(missing_ok=True)


def _range(rows: list[dict[str, Any]], key: str) -> str:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return "n/a"
    return f"{vals[0]} -> {vals[-1]} (min {min(vals)}, max {max(vals)})"


def cmd_summary(args: argparse.Namespace) -> None:
    log = Path(args.log)
    pos_file = log.with_name(log.name + ".pos")
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    start = int(pos_file.read_text(encoding="utf-8")) if pos_file.exists() else 0
    if start > len(lines):  # the log was replaced
        start = 0
    pos_file.write_text(str(len(lines)), encoding="utf-8")
    rows = [json.loads(line) for line in lines[start:] if line.strip()]
    errors = [r for r in rows if "err" in r]
    rows = [r for r in rows if "err" not in r]
    if errors:
        print(f"{len(errors)} failed polls, last: {errors[-1]['err']}")
    if not rows:
        print("no new polls")
        return
    first, last = rows[0], rows[-1]
    print(
        f"{len(rows)} polls, month {first['month']} day {first['day']} -> month {last['month']} day {last['day']}, "
        f"scenario {last['status']}"
    )
    for key in ("cash", "rating", "guests", "cap", "unhappy"):
        print(f"  {key}: {_range(rows, key)}")
    print(
        f"  happiness: {first['avg'].get('happiness')} -> {last['avg'].get('happiness')}, "
        f"nausea {last['avg'].get('nausea')}, toilet {last['avg'].get('toilet')}"
    )
    worst: dict[str, int] = {}
    for row in rows:
        for thought, count in row.get("thoughts", {}).items():
            if thought in PROBLEM_THOUGHTS:
                worst[thought] = max(worst.get(thought, 0), count)
    print("  worst problem thoughts:", dict(sorted(worst.items(), key=lambda kv: -kv[1])))
    rides: dict[str, dict[str, int]] = {}
    for row in rows:
        for ride, problems in row.get("ride_problems", {}).items():
            for problem, count in problems.items():
                rides.setdefault(ride, {})[problem] = max(rides.get(ride, {}).get(problem, 0), count)
    if rides:
        top = sorted(rides.items(), key=lambda kv: -sum(kv[1].values()))[:8]
        print("  ride problems (peak):", dict(top))
    repriced = [change for row in rows for change in row.get("repriced", [])]
    if repriced:
        print(f"  repriced {len(repriced)} times, last: {repriced[-5:]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="one-line park status").set_defaults(func=cmd_status)
    watch = sub.add_parser("watch", help="poll into a JSON-lines log until stopped")
    watch.add_argument("--interval", type=float, default=15.0, help="seconds between polls (default 15)")
    watch.add_argument("--log", default=str(DEFAULT_LOG))
    watch.add_argument("--reprice", action="store_true", help="apply the ride-value price rule every poll")
    watch.set_defaults(func=cmd_watch)
    summary = sub.add_parser("summary", help="summarize polls since the last summary")
    summary.add_argument("--log", default=str(DEFAULT_LOG))
    summary.set_defaults(func=cmd_summary)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
