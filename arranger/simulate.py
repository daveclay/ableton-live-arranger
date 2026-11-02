"""Apply a Plan to an in-memory arrangement, mimicking Live's behavior.

`strict=True` raises if a stage/place lands on top of existing material, which
is the plan's core guarantee (Live would silently overwrite).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List

from .model import EPS, Arrangement, Clip, Cue
from .planner import Plan


class SimulationError(Exception):
    pass


def _at(clips: List[Clip], t: float) -> Clip:
    for c in clips:
        if abs(c.start - t) < EPS:
            return c
    raise SimulationError("no clip at %s" % t)


def apply(arr: Arrangement, plan: Plan, strict: bool = True) -> Arrangement:
    lanes: Dict[int, List[Clip]] = {t.index: list(t.clips) for t in arr.tracks}

    def check_free(ti, a, b, ignore=None):
        for c in lanes[ti]:
            if c is not ignore and c.overlaps(a, b):
                raise SimulationError("track %d: %.3f-%.3f would overwrite clip at %.3f-%.3f"
                                      % (ti, a, b, c.start, c.end))

    for op in plan.ops:
        ti, kind = op["track"], op["op"]
        lane = lanes[ti]
        if kind == "stage":
            src = _at(lane, op["src"])
            full = replace(src, start=op["at"], end=op["at"] + (src.end - src.start))
            if strict:
                check_free(ti, full.start, full.end)
            length = op["loop_end"] - op["loop_start"]
            lane.append(replace(full, loop_start=op["loop_start"], end=op["at"] + length))
        elif kind == "delete":
            lane.remove(_at(lane, op["at"]))
        elif kind == "place":
            src = _at(lane, op["src"])
            new = replace(src, start=op["dest"], end=op["dest"] + (src.end - src.start))
            if strict:
                check_free(ti, new.start, new.end)
            lane.append(new)
        else:
            raise SimulationError("unknown op %r" % kind)

    cues = [c for c in arr.cues if c not in plan.cue_deletes] + list(plan.cue_creates)
    tracks = [replace(t, clips=sorted(lanes[t.index], key=lambda c: c.start)) for t in arr.tracks]
    return Arrangement(tracks, sorted(cues, key=lambda c: c.time), arr.signature, arr.tempo)


def heard(clips: List[Clip], t: float):
    """(source file identity, content position) audible at time t, or None."""
    for c in clips:
        if c.start - EPS <= t < c.end - EPS:
            return (c.name, round(c.content_at(t), 4))
    return None
