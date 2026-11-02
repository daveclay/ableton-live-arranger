import itertools
import json
import os
import unittest

from arranger.model import Arrangement, Clip, Cue, Track
from arranger.planner import PlanError, plan_copy, plan_move
from arranger.simulate import apply, heard

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "arranger_test_state.json")


def load_fixture():
    with open(FIXTURE) as f:
        return Arrangement.from_state(json.load(f))


def sample_times(a, b, step=0.25):
    t = a + 0.01
    while t < b - 0.01:
        yield t
        t += step


class Verify:
    def verify(self, before, plan, tracks):
        after = apply(before, plan, strict=True)
        # Every point of every new section sounds like the matching source point.
        for ti in tracks:
            old, new = before.tracks[ti].clips, after.tracks[ti].clips
            for src, new_start in plan.mapping:
                for t in sample_times(new_start, new_start + src.length):
                    self.assertEqual(heard(new, t), heard(old, src.start + (t - new_start)),
                                     "%s: track %d at %.2f" % (plan.description, ti, t))
            # Material before the first section is untouched.
            first = plan.mapping[0][1]
            for t in sample_times(0, first):
                self.assertEqual(heard(new, t), heard(old, t))
            # No scratch leftovers past the new song end.
            end = max(s.end for s in plan.new_sections)
            self.assertFalse([c for c in new if c.start > end + 1e-3], "scratch clip left behind")
            # No overlaps on the lane.
            for x, y in zip(new, new[1:]):
                self.assertLessEqual(x.end, y.start + 1e-3)
        # Unpicked tracks unchanged.
        for t in before.tracks:
            if t.index not in tracks:
                self.assertEqual(t.clips, after.tracks[t.index].clips)
        return after


class TestOnFixture(unittest.TestCase, Verify):
    @classmethod
    def setUpClass(cls):
        cls.arr = load_fixture()
        cls.audio = [t.index for t in cls.arr.tracks if t.clips]

    def test_sections(self):
        names = [s.name for s in self.arr.sections(self.audio)]
        self.assertEqual(names, ["A", "B", "C", "D", "E", "F"])

    def test_every_move(self):
        names = [s.name for s in self.arr.sections(self.audio)]
        for src, dst in itertools.permutations(names, 2):
            for kw in ({"after": dst}, {"before": dst}):
                plan = plan_move(self.arr, self.audio, src, **kw)
                if plan.ops:
                    self.verify(self.arr, plan, self.audio)

    def test_every_copy(self):
        names = [s.name for s in self.arr.sections(self.audio)]
        for src, dst in itertools.product(names, names):
            plan = plan_copy(self.arr, self.audio, src, after=dst)
            self.verify(self.arr, plan, self.audio)

    def test_subset_of_tracks(self):
        drums = [1, 2, 3, 4]
        plan = plan_move(self.arr, drums, "B", after="C")
        after = self.verify(self.arr, plan, drums)
        self.assertEqual(after.tracks[7].clips, self.arr.tracks[7].clips)

    def test_move_b_after_c_locators(self):
        plan = plan_move(self.arr, self.audio, "B", after="C")
        self.assertEqual([s.name for s in plan.new_sections], ["A", "C", "B", "D", "E", "F"])
        after = apply(self.arr, plan)
        self.assertEqual([(c.name, c.time) for c in after.cues][:4],
                         [("A", 16.0), ("C", 56.0), ("B", 72.0), ("D", 184.0)])

    def test_copy_shifts_tail(self):
        plan = plan_copy(self.arr, self.audio, "C", after="D")
        after = apply(self.arr, plan)
        self.assertEqual([c.name for c in after.cues], ["A", "B", "C", "D", "C 2", "E", "F"])
        self.assertEqual(dict((c.name, c.time) for c in after.cues)["E"], 240.0 + 16.0)

    def test_noop(self):
        plan = plan_move(self.arr, self.audio, "B", after="A")
        self.assertEqual(plan.ops, [])


class TestSmall(unittest.TestCase, Verify):
    def arr(self, clips):
        return Arrangement([Track(0, "t", clips)], [Cue("A", 0), Cue("B", 8), Cue("C", 16)])

    def test_clip_spanning_everything(self):
        arr = self.arr([Clip(0, 0, 24, 100.0, "long")])
        self.verify(arr, plan_move(arr, [0], "A", after="C"), [0])

    def test_gaps_preserved(self):
        arr = self.arr([Clip(0, 1, 3, 0, "x"), Clip(0, 10, 12, 0, "y"), Clip(0, 20, 22, 0, "z")])
        after = self.verify(arr, plan_move(arr, [0], "C", before="A"), [0])
        self.assertEqual([(c.name, c.start, c.end) for c in after.tracks[0].clips],
                         [("z", 4, 6), ("x", 9, 11), ("y", 18, 20)])

    def test_rejects_looped_clip(self):
        arr = self.arr([Clip(0, 0, 24, 0, "loop", looping=True)])
        with self.assertRaises(PlanError):
            plan_move(arr, [0], "A", after="B")

    def test_unknown_section(self):
        arr = self.arr([])
        with self.assertRaises(PlanError):
            plan_move(arr, [0], "Z", after="A")


if __name__ == "__main__":
    unittest.main()
