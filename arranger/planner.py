"""Plan section moves/copies as a list of primitive clip and locator operations.

The plan never overwrites and never leaves gaps:

1. **stage**  — every clip piece that needs to end up somewhere new is duplicated
   into a scratch area past the end of the song and trimmed there (Live's
   duplicate overwrites whatever lies under the *full* clip length, so trimming
   must happen away from real material).
2. **delete** — original clips overlapping the rewritten region are removed
   (their parts outside the region were staged as "keep" pieces).
3. **place**  — trimmed scratch pieces are duplicated to their destinations,
   which are now empty.
4. **unstage** — scratch clips are deleted.
5. Locators for affected sections are deleted and recreated at their new times.

Only picked tracks are touched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .model import EPS, Arrangement, Clip, Cue, Section


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class Piece:
    clip: Clip
    a: float  # timeline range of the original clip to take
    b: float
    dest: float  # where the piece's start lands

    @property
    def length(self):
        return self.b - self.a


@dataclass
class Plan:
    description: str
    tracks: List[int]
    ops: List[dict] = field(default_factory=list)
    cue_deletes: List[Cue] = field(default_factory=list)
    cue_creates: List[Cue] = field(default_factory=list)
    new_sections: List[Section] = field(default_factory=list)
    mapping: List[tuple] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "ops": self.ops,
            "cue_deletes": [c.time for c in self.cue_deletes],
            "cue_creates": [[c.name, c.time] for c in self.cue_creates],
        }


@dataclass(frozen=True)
class Placement:
    section: Section
    new_start: float
    is_copy: bool = False
    name: str = ""


def _find(sections: Sequence[Section], name: str) -> int:
    for i, s in enumerate(sections):
        if s.name == name:
            return i
    raise PlanError("no section named %r (have: %s)" % (name, ", ".join(s.name for s in sections)))


def _insert_index(sections, after: Optional[str], before: Optional[str]) -> int:
    if (after is None) == (before is None):
        raise PlanError("give exactly one of after= / before=")
    return _find(sections, after) + 1 if after is not None else _find(sections, before)


def _layout(sections: Sequence[Section], order: Sequence[tuple]) -> List[Placement]:
    """order: [(section, is_copy, name)] -> placements packed end to end."""
    t = sections[0].start
    out = []
    for sec, is_copy, name in order:
        out.append(Placement(sec, t, is_copy, name or sec.name))
        t += sec.length
    return out


def _copy_name(existing: Sequence[str], base: str) -> str:
    n = 2
    while "%s %d" % (base, n) in existing:
        n += 1
    return "%s %d" % (base, n)


def plan_move(arr: Arrangement, tracks: Sequence[int], name: str,
              after: Optional[str] = None, before: Optional[str] = None) -> Plan:
    sections = arr.sections(tracks)
    src = _find(sections, name)
    dst = _insert_index(sections, after, before)
    order = [(s, False, "") for s in sections]
    item = order.pop(src)
    order.insert(dst - 1 if dst > src else dst, item)
    desc = "move %s %s %s" % (name, "after" if after else "before", after or before)
    return _build(arr, tracks, sections, _layout(sections, order), desc)


def plan_copy(arr: Arrangement, tracks: Sequence[int], name: str,
              after: Optional[str] = None, before: Optional[str] = None,
              new_name: Optional[str] = None) -> Plan:
    sections = arr.sections(tracks)
    src = _find(sections, name)
    dst = _insert_index(sections, after, before)
    new_name = new_name or _copy_name([c.name for c in arr.cues], name)
    order = [(s, False, "") for s in sections]
    order.insert(dst, (sections[src], True, new_name))
    desc = "copy %s %s %s (as %s)" % (name, "after" if after else "before", after or before, new_name)
    return _build(arr, tracks, sections, _layout(sections, order), desc)


def _build(arr, tracks, sections, layout, desc) -> Plan:
    plan = Plan(desc, list(tracks))
    plan.new_sections = [Section(p.name, p.new_start, p.new_start + p.section.length) for p in layout]
    plan.mapping = [(p.section, p.new_start) for p in layout]  # (source section, new start)

    affected = [p for p in layout if p.is_copy or abs(p.new_start - p.section.start) > EPS]
    if not affected:
        plan.warnings.append("nothing to do: section is already there")
        return plan

    bar = arr.beats_per_bar
    for p in affected:
        bars = p.section.length / bar
        if abs(bars - round(bars)) > 0.01:
            plan.warnings.append(
                "section %s is %.3f bars (not whole bars) — later sections will land off the bar grid; "
                "check its locators" % (p.section.name, bars))

    moved = [p.section for p in affected if not p.is_copy]
    region = (min(s.start for s in moved), max(s.end for s in moved)) if moved else None

    new_end = max(s.end for s in plan.new_sections)
    scratch_base = math.ceil((max(new_end, arr.content_end(range(len(arr.tracks)))) + 16 * bar) / bar) * bar

    bad = []
    for ti in tracks:
        track = arr.tracks[ti]
        pieces: List[Piece] = []
        deletes: List[Clip] = []
        for p in affected:
            s = p.section
            for c in track.clips:
                if c.overlaps(s.start, s.end):
                    a, b = max(c.start, s.start), min(c.end, s.end)
                    pieces.append(Piece(c, a, b, p.new_start + (a - s.start)))
        if region:
            r0, r1 = region
            for c in track.clips:
                if c.overlaps(r0, r1):
                    deletes.append(c)
                    if c.start < r0 - EPS:
                        pieces.append(Piece(c, c.start, r0, c.start))
                    if c.end > r1 + EPS:
                        pieces.append(Piece(c, r1, c.end, r1))

        for c in {pc.clip for pc in pieces} | set(deletes):
            if c.looping:
                bad.append("%s: clip %r at bar %.2f is looped" % (track.name, c.name, arr.bar_of(c.start)))
            elif c.is_audio and not c.warping:
                bad.append("%s: clip %r at bar %.2f is not warped" % (track.name, c.name, arr.bar_of(c.start)))

        cursor = scratch_base
        staged = []
        for pc in sorted(pieces, key=lambda x: x.dest):
            plan.ops.append({
                "op": "stage", "track": ti, "src": pc.clip.start, "at": cursor,
                "loop_start": pc.clip.content_at(pc.a), "loop_end": pc.clip.content_at(pc.b),
            })
            staged.append((cursor, pc.dest))
            cursor += math.ceil((pc.length + bar) / bar) * bar  # keep a gap between scratch pieces
        for c in sorted(deletes, key=lambda x: x.start):
            plan.ops.append({"op": "delete", "track": ti, "at": c.start})
        for at, dest in staged:
            plan.ops.append({"op": "place", "track": ti, "src": at, "dest": dest})
        for at, _ in staged:
            plan.ops.append({"op": "delete", "track": ti, "at": at})

    if bad:
        raise PlanError("unsupported clips (unloop / warp them first):\n  " + "\n  ".join(bad))

    # Locators: recreate those whose section start changed.
    by_time = {round(c.time, 3): c for c in arr.cues}
    for p in affected:
        if not p.is_copy:
            old = by_time.get(round(p.section.start, 3))
            if old:
                plan.cue_deletes.append(old)
        plan.cue_creates.append(Cue(p.name, p.new_start))
    return plan


def describe(arr: Arrangement, plan: Plan, track_names: Sequence[str]) -> str:
    lines = [plan.description, "tracks: " + ", ".join(track_names)]
    lines.append("new order: " + "  ".join(
        "%s@%g" % (s.name, round(arr.bar_of(s.start), 2)) for s in plan.new_sections))
    counts = {}
    for op in plan.ops:
        counts[op["op"]] = counts.get(op["op"], 0) + 1
    lines.append("clip ops: " + ", ".join("%d %s" % (n, k) for k, n in counts.items()))
    lines.append("locators: %d removed, %d created" % (len(plan.cue_deletes), len(plan.cue_creates)))
    for w in plan.warnings:
        lines.append("warning: " + w)
    return "\n".join(lines)
