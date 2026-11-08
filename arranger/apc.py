"""Drive the arranger from an Akai APC40 mkII.

    .venv/bin/python -m arranger.apc

Controls (edit the MAP below to change them):
  Track select 1-8      toggle that track in the pick set (group = all its tracks)
  Bank ◄ / ►            previous / next 8 tracks
  Cue Level knob        move the track cursor (Live's selected track follows)
  Device On/Off         toggle the cursor's track       (Shift: clear all picks)
  Clip pads             sections in song order, 8 per row from the top-left:
                          tap a section to pick it up (white),
                          tap another to MOVE it in front of that one,
                          hold Shift on the second tap to COPY instead,
                          tap the dim pad after the last section to put it at the end,
                          tap the picked-up section again to cancel
  Device ◄              undo the last move/copy
  Device Lock           add a locator at the playhead   (Shift: remove the one there)
  Tempo knob            move the playhead by bars (Shift: by 16th notes)
Clip-stop lights show the cursor; track-select lights show picked tracks.
Messages appear in Live's status bar.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

from .client import LiveClient, LiveError
from .model import Arrangement
from .planner import PlanError, plan_copy, plan_move
from .runner import automation_warnings, execute

PORT = "APC40 mkII"
SYSEX_ABLETON_MODE = [0x47, 0x7F, 0x29, 0x60, 0x00, 0x04, 0x41, 0x08, 0x02, 0x01]

MAP = {
    "track_select": 51,   # note, channel = column 0-7
    "clip_stop": 52,      # note, channel = column 0-7 (cursor light)
    "bank_left": 97,
    "bank_right": 96,
    "shift": 98,
    "toggle_cursor": 62,  # Device On/Off
    "undo": 58,           # Device ◄
    "locator": 63,        # Device Lock
    "cursor_knob": 47,    # CC, relative
    "transport_knob": 13, # Tempo knob, CC, relative
}
KNOB_STEP = 2  # knob ticks per track step (raise to make the cursor slower)
TRANSPORT_STEP = 1  # knob ticks per step (bar; with Shift a 16th note)

# APC40 mkII pad colour palette (velocity values).
OFF, DIM, WHITE = 0, 1, 3
SECTION_COLORS = [5, 9, 13, 21, 33, 45, 49, 53]  # red orange yellow green cyan blue purple pink
BLINK_CHANNEL = 14  # pad LED channel: blinking (0 = solid)
ROWS, COLS = 5, 8


def pad_note(index: int) -> int:
    """Section slot index (reading order from top-left) -> pad note (0 = bottom-left)."""
    row, col = divmod(index, COLS)
    return (ROWS - 1 - row) * COLS + col


def pad_index(note: int) -> int:
    row_from_bottom, col = divmod(note, COLS)
    return (ROWS - 1 - row_from_bottom) * COLS + col


class Controller:
    def __init__(self, client, send: Callable[[str, int, int, int], None], log=print):
        """send(kind, channel, number, value) with kind 'note' or 'cc'."""
        self.client = client
        self._send = send
        self.log = log
        self.arr: Optional[Arrangement] = None
        self.is_playing = False
        self.picked: Set[int] = set()
        self.cursor = 0
        self.bank = 0
        self.shift = False
        self.held: Optional[int] = None  # picked-up section index
        self.knob_acc = 0
        self.transport_acc = 0
        self.pending_nudge: Dict[str, int] = {}  # unit -> steps, sent by flush()
        self.leds: Dict[Tuple[str, int, int], int] = {}

    # ---- Live state -------------------------------------------------------
    def refresh(self):
        state = self.client.call("state")
        self.arr = Arrangement.from_state(state)
        self.is_playing = state.get("is_playing", False)
        n = len(self.arr.tracks)
        self.picked = {i for i in self.picked if i < n}
        sel = state.get("selected_track")
        if sel is not None:
            self.cursor = sel
        if self.held is not None and self.held >= len(self.sections()):
            self.held = None
        self.render()

    def sections(self):
        return self.arr.sections(self.pick_list() or [t.index for t in self.arr.tracks if t.clips])

    def pick_list(self) -> List[int]:
        return sorted(self.picked)

    def say(self, text):
        self.log(text)
        try:
            self.client.call("message", text="Arranger: " + text)
        except (LiveError, OSError):
            pass

    # ---- tracks -------------------------------------------------------------
    def members(self, ti) -> List[int]:
        t = self.arr.tracks[ti]
        if t.is_group:
            return [m.index for m in self.arr.tracks if m.group == t.name]
        return [ti]

    def is_picked(self, ti) -> bool:
        m = self.members(ti)
        return bool(m) and all(i in self.picked for i in m)

    def toggle_track(self, ti):
        if ti >= len(self.arr.tracks):
            return
        m = self.members(ti)
        if self.is_picked(ti):
            self.picked.difference_update(m)
        else:
            self.picked.update(m)
        names = [self.arr.tracks[i].name for i in self.pick_list()]
        self.say("picked: " + (", ".join(names) or "none"))

    def select(self, ti):
        """Move the cursor to track ti and select it in Live."""
        self.cursor = ti
        try:
            self.client.call("select_track", track=ti)
        except LiveError as e:
            self.log("can't select track: %s" % e)

    def move_cursor(self, delta):
        n = len(self.arr.tracks)
        new = max(0, min(n - 1, self.cursor + delta))
        if new != self.cursor:
            self.bank = (new // COLS) * COLS
            self.select(new)

    # ---- sections -----------------------------------------------------------
    def press_pad(self, slot):
        secs = self.sections()
        if self.held is None:
            if slot < len(secs):
                self.held = slot
                self.say("picked up %s — tap where it should go%s" % (
                    secs[slot].name, "" if self.picked else " (no tracks picked!)"))
            return
        src, self.held = self.held, None
        if slot == src:
            self.say("cancelled")
            return
        if slot > len(secs):
            self.say("cancelled")
            return
        self.edit(secs, src, slot, copy=self.shift)

    def edit(self, secs, src, slot, copy):
        tracks = self.pick_list()
        if not tracks:
            self.say("pick some tracks first")
            return
        if self.is_playing:
            self.say("stop the transport first")
            return
        names = [s.name for s in secs]
        if len(set(names)) != len(names):
            self.say("two sections share a name; rename a locator")
            return
        kw = {"after": names[-1]} if slot == len(secs) else {"before": names[slot]}
        try:
            fn = plan_copy if copy else plan_move
            plan = fn(self.arr, tracks, names[src], **kw)
            if not plan.ops:
                self.say("already there")
                return
            for w in plan.warnings + automation_warnings(self.client, tracks):
                self.log("warning: " + w)
            self.say("%s…" % plan.description)
            problems = execute(self.client, self.arr, plan)
            self.say(("VERIFY FAILED: " + problems[0]) if problems else "done: " + plan.description)
        except (PlanError, LiveError) as e:
            self.say("error: %s" % e)
        finally:
            self.refresh()

    def locator(self, add):
        self.flush()  # land any knob turn first
        try:
            res = self.client.run_job("locator_at_playhead", add=add)
        except LiveError as e:
            self.say("locator: %s" % e)
            return
        bar = "bar %g" % round(self.arr.bar_of(res["time"]), 2)
        if res["done"] and add:
            name = self.ask_name(res["name"], bar)
            if name and name != res["name"]:
                try:
                    res["name"] = self.client.call("rename_locator", time=res["time"], name=name)
                except LiveError as e:
                    self.log("couldn't rename: %s" % e)
            self.say("added locator %s at %s" % (res["name"], bar))
        elif res["done"]:
            self.say("removed locator at " + bar)
        else:
            self.say(("locator %s is already at %s" % (res["name"], bar)) if add else "no locator at " + bar)
        self.refresh()

    def ask_name(self, default, bar):
        """Pop up a macOS text box for the new locator's name; None if cancelled."""
        script = ('activate\n'
                  'display dialog "Name the locator at %s" default answer "%s" '
                  'with title "Arranger"\n'
                  'text returned of result' % (bar, default.replace('"', '')))
        try:
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def undo(self):
        try:
            res = self.client.run_job("arrange_undo")
            self.say("undone (%d steps)" % res["undo_steps"])
        except LiveError as e:
            self.say("can't undo: %s" % e)

    # ---- MIDI in ------------------------------------------------------------
    def handle(self, kind, channel, number, value):
        """kind: 'note_on' (value 0 = release), 'cc'."""
        if kind == "note_on":
            if number == MAP["shift"]:
                self.shift = value > 0
                return
            if value == 0:
                return
            if number < ROWS * COLS and channel == 0:
                self.press_pad(pad_index(number))
            elif number == MAP["track_select"]:
                ti = self.bank + channel
                if ti < len(self.arr.tracks):
                    self.select(ti)  # show it in Live so you can see which track it is
                    self.toggle_track(ti)
            elif number == MAP["bank_left"]:
                self.bank = max(0, self.bank - COLS)
            elif number == MAP["bank_right"]:
                if self.bank + COLS < len(self.arr.tracks):
                    self.bank += COLS
            elif number == MAP["toggle_cursor"]:
                if self.shift:
                    self.picked.clear()
                    self.say("cleared picks")
                else:
                    self.toggle_track(self.cursor)
            elif number == MAP["locator"]:
                self.locator(add=not self.shift)
            elif number == MAP["undo"]:
                self.undo()
                self.refresh()
            else:
                return
        elif kind == "cc" and number == MAP["cursor_knob"]:
            self.knob_acc += value if value < 64 else value - 128
            steps = int(self.knob_acc / KNOB_STEP)
            if steps:
                self.knob_acc -= steps * KNOB_STEP
                self.move_cursor(steps)
        elif kind == "cc" and number == MAP["transport_knob"]:
            self.transport_acc += value if value < 64 else value - 128
            steps = int(self.transport_acc / TRANSPORT_STEP)
            if steps:
                self.transport_acc -= steps * TRANSPORT_STEP
                unit = "sixteenth" if self.shift else "bar"
                self.pending_nudge[unit] = self.pending_nudge.get(unit, 0) + steps
            return
        else:
            return
        self.render()

    def flush(self):
        """Send knob turns collected since the last flush as one playhead move.

        Each call to Live waits for its next tick (~100 ms), so sending every
        click separately made the playhead lag behind the knob."""
        pending, self.pending_nudge = self.pending_nudge, {}
        for unit, steps in pending.items():
            if steps:
                try:
                    self.client.call("nudge_playhead", steps=steps, unit=unit)
                except LiveError as e:
                    self.log("can't move playhead: %s" % e)

    # ---- LEDs ---------------------------------------------------------------
    def led(self, kind, channel, number, value):
        key = (kind, channel, number)
        if self.leds.get(key) != value:
            self.leds[key] = value
            self._send(kind, channel, number, value)

    def render(self):
        if self.arr is None:
            return
        n = len(self.arr.tracks)
        for col in range(COLS):
            ti = self.bank + col
            self.led("note", col, MAP["track_select"], 1 if ti < n and self.is_picked(ti) else 0)
            self.led("note", col, MAP["clip_stop"], 1 if ti == self.cursor else 0)
        secs = self.sections()
        for slot in range(ROWS * COLS):
            note = pad_note(slot)
            if slot < len(secs):
                if slot == self.held:
                    color, ch = WHITE, BLINK_CHANNEL
                else:
                    color, ch = SECTION_COLORS[slot % len(SECTION_COLORS)], 0
            elif slot == len(secs) and self.held is not None:
                color, ch = DIM, 0  # "put it at the end"
            else:
                color, ch = OFF, 0
            # A pad has one colour at a time; clear the other channel when switching.
            other = 0 if ch else BLINK_CHANNEL
            if self.leds.get(("note", other, note)):
                self.led("note", other, note, 0)
            self.led("note", ch, note, color)

    def all_off(self):
        for (kind, ch, num), v in list(self.leds.items()):
            if v:
                self._send(kind, ch, num, 0)
        self.leds.clear()


def main():
    import mido

    try:
        inp, out = mido.open_input(PORT), mido.open_output(PORT)
    except (IOError, OSError) as e:
        sys.exit("can't open %s: %s" % (PORT, e))

    def send(kind, ch, num, val):
        out.send(mido.Message("note_on", channel=ch, note=num, velocity=val) if kind == "note"
                 else mido.Message("control_change", channel=ch, control=num, value=val))

    out.send(mido.Message("sysex", data=SYSEX_ABLETON_MODE))
    client = connect()
    ctl = Controller(client, send)
    ctl.refresh()
    ctl.say("APC ready — %d sections" % len(ctl.sections()))
    last_refresh = time.monotonic()
    try:
        while True:
            try:
                for msg in inp.iter_pending():
                    if msg.type in ("note_on", "note_off"):
                        ctl.handle("note_on", msg.channel, msg.note, 0 if msg.type == "note_off" else msg.velocity)
                    elif msg.type == "control_change":
                        ctl.handle("cc", msg.channel, msg.control, msg.value)
                ctl.flush()
                if time.monotonic() - last_refresh > 1.0:
                    ctl.refresh()
                    last_refresh = time.monotonic()
            except OSError as e:  # Live quit, reloaded the set, or dropped the socket
                print("lost Live (%s); reconnecting…" % e, flush=True)
                ctl.all_off()  # dark pads = not connected
                client.close()
                client = ctl.client = connect()
                for _ in inp.iter_pending():
                    pass  # drop presses made while disconnected
                ctl.held, ctl.pending_nudge = None, {}
                ctl.refresh()
                ctl.say("APC reconnected — %d sections" % len(ctl.sections()))
                last_refresh = time.monotonic()
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        ctl.all_off()
        client.close()
        inp.close()
        out.close()


def connect():
    """Connect to LiveArranger in Live, waiting until it's there."""
    waiting = False
    while True:
        try:
            client = LiveClient()
            client.call("ping")
            return client
        except OSError:
            if not waiting:
                print("waiting for LiveArranger in Live…", flush=True)
                waiting = True
            time.sleep(1.0)

if __name__ == "__main__":
    main()
