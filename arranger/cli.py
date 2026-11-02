"""Command line: python3 -m arranger <command> ...

  tracks                              list tracks
  sections [--tracks T]               list sections (locator to locator)
  move NAME --after X|--before X --tracks T [--dry-run] [-y]
  copy NAME --after X|--before X --tracks T [--as NEW] [--dry-run] [-y]

T is a comma-separated list of track names or indices; a group name picks all
tracks in the group; "all" picks every track with arrangement clips.
"""

from __future__ import annotations

import argparse
import sys

from .client import LiveClient, LiveError
from .model import EPS, Arrangement
from .planner import PlanError, describe, plan_copy, plan_move
from .runner import automation_warnings, execute
from .simulate import apply


def resolve_tracks(arr: Arrangement, spec: str):
    if spec == "all":
        return [t.index for t in arr.tracks if t.clips]
    picked = []
    for item in (s.strip() for s in spec.split(",") if s.strip()):
        if item.isdigit():
            matches = [arr.tracks[int(item)]]
        else:
            matches = [t for t in arr.tracks if t.name == item]
            if not matches:
                raise PlanError("no track named %r" % item)
            if len(matches) > 1:
                raise PlanError("several tracks named %r; use an index" % item)
        t = matches[0]
        members = [m.index for m in arr.tracks if m.group == t.name] if t.is_group else [t.index]
        picked += [i for i in members if i not in picked]
    return picked


def bar(arr, beats):
    return "%g" % round(arr.bar_of(beats), 2)


def cmd_tracks(arr, args, client):
    for t in arr.tracks:
        kind = "group" if t.is_group else "%d clips" % len(t.clips)
        print("%3d  %-20s %-10s %s" % (t.index, t.name, kind, ("in " + t.group) if t.group else ""))


def cmd_sections(arr, args, client):
    tracks = resolve_tracks(arr, args.tracks)
    for s in arr.sections(tracks):
        print("%-10s bar %-7s - %-7s (%g bars)" % (s.name, bar(arr, s.start), bar(arr, s.end),
                                                  round(s.length / arr.beats_per_bar, 3)))


def cmd_undo(arr, args, client):
    res = client.run_job("arrange_undo")
    print("undone (%d Live undo steps)" % res["undo_steps"])


def cmd_edit(arr, args, client):
    tracks = resolve_tracks(arr, args.tracks)
    if args.command == "move":
        plan = plan_move(arr, tracks, args.name, after=args.after, before=args.before)
    else:
        plan = plan_copy(arr, tracks, args.name, after=args.after, before=args.before, new_name=args.new_name)
    print(describe(arr, plan, [arr.tracks[i].name for i in tracks]))
    for w in automation_warnings(client, tracks):
        print("warning: " + w)
    if not plan.ops or args.dry_run:
        return
    apply(arr, plan, strict=True)  # refuse anything that would overwrite, before asking
    if not args.yes and input("apply? [y/N] ").strip().lower() != "y":
        print("cancelled")
        return
    problems = execute(client, arr, plan)
    if problems:
        print("VERIFY FAILED (try `python3 -m arranger undo`):\n  " + "\n  ".join(problems))
        sys.exit(2)
    print("verified: Live matches the plan. `python3 -m arranger undo` reverts it.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="arranger", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("tracks")
    sub.add_parser("undo", help="undo the last move/copy (only if nothing else changed since)")
    s = sub.add_parser("sections")
    s.add_argument("--tracks", default="all")
    for name in ("move", "copy"):
        e = sub.add_parser(name)
        e.add_argument("name")
        g = e.add_mutually_exclusive_group(required=True)
        g.add_argument("--after")
        g.add_argument("--before")
        e.add_argument("--tracks", required=True)
        e.add_argument("--dry-run", action="store_true")
        e.add_argument("-y", "--yes", action="store_true")
        if name == "copy":
            e.add_argument("--as", dest="new_name")
    args = p.parse_args(argv)

    try:
        with LiveClient() as client:
            arr = Arrangement.from_state(client.call("state"))
            {"tracks": cmd_tracks, "sections": cmd_sections, "undo": cmd_undo}.get(
                args.command, cmd_edit)(arr, args, client)
    except ConnectionRefusedError:
        sys.exit("can't reach LiveArranger in Live (is it selected as a Control Surface?)")
    except (PlanError, LiveError) as e:
        sys.exit("error: %s" % e)


if __name__ == "__main__":
    main()
