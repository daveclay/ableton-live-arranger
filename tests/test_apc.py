import json
import os
import unittest
from unittest import mock

from arranger import apc
from arranger.apc import Controller, pad_index, pad_note

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "arranger_test_state.json")


class FakeClient:
    def __init__(self):
        with open(FIXTURE) as f:
            self.state = json.load(f)
        self.state.update(selected_track=0, is_playing=False)
        self.calls = []

    def call(self, cmd, **args):
        self.calls.append((cmd, args))
        if cmd == "state":
            return self.state
        if cmd == "automation_report":
            return []
        return "ok"


def make():
    sent = []
    ctl = Controller(FakeClient(), lambda *a: sent.append(a), log=lambda *_: None)
    ctl.refresh()
    return ctl, sent


class TestPads(unittest.TestCase):
    def test_mapping_roundtrip(self):
        self.assertEqual(pad_note(0), 32)   # top-left
        self.assertEqual(pad_note(39), 7)   # bottom-right
        for i in range(40):
            self.assertEqual(pad_index(pad_note(i)), i)

    def test_sections_lit(self):
        ctl, sent = make()
        lit = {n for (kind, ch, n, v) in sent if kind == "note" and ch == 0 and n < 40 and v}
        self.assertEqual(lit, {pad_note(i) for i in range(6)})  # A-F


class TestTracks(unittest.TestCase):
    def test_group_toggle_picks_children(self):
        ctl, _ = make()
        ctl.handle("note_on", 0, apc.MAP["track_select"], 127)  # column 0 = drm grp
        self.assertEqual(ctl.pick_list(), [1, 2, 3, 4, 5])
        ctl.handle("note_on", 0, apc.MAP["track_select"], 127)
        self.assertEqual(ctl.pick_list(), [])

    def test_track_select_selects_in_live(self):
        ctl, _ = make()
        ctl.handle("note_on", 7, apc.MAP["track_select"], 127)  # column 7, a plain track
        self.assertEqual(ctl.cursor, 7)
        self.assertIn(("select_track", {"track": 7}), ctl.client.calls)
        self.assertEqual(ctl.pick_list(), [7])

    def test_knob_moves_cursor_and_selects_in_live(self):
        ctl, _ = make()
        for _ in range(4):
            ctl.handle("cc", 0, apc.MAP["cursor_knob"], 1)
        self.assertEqual(ctl.cursor, 2)
        ctl.handle("cc", 0, apc.MAP["cursor_knob"], 126)  # -2
        self.assertEqual(ctl.cursor, 1)
        self.assertIn(("select_track", {"track": 1}), ctl.client.calls)

    def test_toggle_cursor_and_shift_clear(self):
        ctl, _ = make()
        ctl.cursor = 7
        ctl.handle("note_on", 0, apc.MAP["toggle_cursor"], 127)
        self.assertEqual(ctl.pick_list(), [7])
        ctl.handle("note_on", 0, apc.MAP["shift"], 127)
        ctl.handle("note_on", 0, apc.MAP["toggle_cursor"], 127)
        self.assertEqual(ctl.pick_list(), [])


class TestEdits(unittest.TestCase):
    def press(self, ctl, slot):
        ctl.handle("note_on", 0, pad_note(slot), 127)

    def test_move_before(self):
        ctl, _ = make()
        ctl.picked = {1, 2, 3}
        with mock.patch.object(apc, "execute", return_value=[]) as ex:
            self.press(ctl, 1)   # B
            self.press(ctl, 3)   # before D  => after C
        plan = ex.call_args[0][2]
        self.assertEqual([s.name for s in plan.new_sections], ["A", "C", "B", "D", "E", "F"])
        self.assertEqual(plan.tracks, [1, 2, 3])

    def test_shift_copies_to_end(self):
        ctl, _ = make()
        ctl.picked = {1}
        with mock.patch.object(apc, "execute", return_value=[]) as ex:
            self.press(ctl, 2)   # C
            ctl.handle("note_on", 0, apc.MAP["shift"], 127)
            self.press(ctl, 6)   # the slot after F = end
        plan = ex.call_args[0][2]
        self.assertEqual([s.name for s in plan.new_sections][-1], "C 2")

    def test_needs_picked_tracks(self):
        ctl, _ = make()
        with mock.patch.object(apc, "execute") as ex:
            self.press(ctl, 1)
            self.press(ctl, 3)
        ex.assert_not_called()

    def test_tap_same_pad_cancels(self):
        ctl, _ = make()
        ctl.picked = {1}
        with mock.patch.object(apc, "execute") as ex:
            self.press(ctl, 1)
            self.assertEqual(ctl.held, 1)
            self.press(ctl, 1)
        self.assertIsNone(ctl.held)
        ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
