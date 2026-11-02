"""Listen-only MIDI monitor (sends nothing). Usage: midi_listen.py [seconds] [port]"""

import sys
import time

import mido

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20
port = sys.argv[2] if len(sys.argv) > 2 else "APC40 mkII"
with mido.open_input(port) as inp:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        for msg in inp.iter_pending():
            if not (msg.type == "control_change" and msg.control == 16):  # skip jittery knob
                print(msg, flush=True)
        time.sleep(0.002)
