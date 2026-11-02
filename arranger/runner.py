"""Execute a Plan against Live and verify the result. Shared by the CLI and APC."""

from __future__ import annotations

from typing import List

from .model import Arrangement
from .planner import Plan
from .simulate import apply


def automation_warnings(client, tracks) -> List[str]:
    return ["%s has automation (%s) that will NOT move" % (name, ", ".join(params))
            for name, params in client.call("automation_report", tracks=tracks)]


def verify(client, tracks, expected: Arrangement) -> List[str]:
    actual = Arrangement.from_state(client.call("state"))
    key = lambda t: [(round(c.start, 3), round(c.end, 3), round(c.loop_start, 3)) for c in t.clips]
    problems = ["track %s: expected %s, got %s" % (actual.tracks[ti].name, key(expected.tracks[ti]), key(actual.tracks[ti]))
                for ti in tracks if key(expected.tracks[ti]) != key(actual.tracks[ti])]
    cues = lambda a: [(c.name, round(c.time, 3)) for c in a.cues]
    if cues(expected) != cues(actual):
        problems.append("locators: expected %s, got %s" % (cues(expected), cues(actual)))
    return problems


def execute(client, arr: Arrangement, plan: Plan) -> List[str]:
    """Simulate (refusing any overwrite), apply in Live, verify. Returns problems (empty = success)."""
    expected = apply(arr, plan, strict=True)
    client.run_job("apply_plan", **plan.to_json())
    return verify(client, plan.tracks, expected)
