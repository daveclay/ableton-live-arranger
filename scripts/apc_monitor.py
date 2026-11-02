"""Print every MIDI message from the APC40 mkII (Ctrl+C to stop).

Puts the APC into "Ableton Live mode" so the host controls the LEDs, and
lights each pad you press so you can see feedback works.

    .venv/bin/python scripts/apc_monitor.py [seconds]
"""

import sys
import time

import mido

PORT = "APC40 mkII"
# Akai SysEx: set mode. 0x40 generic, 0x41 Ableton Live, 0x42 alternate Ableton Live.
MODE_ABLETON = [0x47, 0x7F, 0x29, 0x60, 0x00, 0x04, 0x41, 0x08, 0x02, 0x01]

with mido.open_input(PORT) as inp, mido.open_output(PORT) as out:
    out.send(mido.Message("sysex", data=MODE_ABLETON))
    print("APC in Ableton Live mode. Press buttons / turn knobs (Ctrl+C to quit).")
    lit = set()
    deadline = time.monotonic() + float(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        while deadline is None or time.monotonic() < deadline:
            msg = inp.poll()
            if msg is None:
                time.sleep(0.002)
                continue
            print(msg, flush=True)
            # Clip-launch pads are notes 0-39 on channel 0; toggle a colour on press.
            if msg.type == "note_on" and msg.velocity and msg.channel == 0 and msg.note < 40:
                on = msg.note not in lit
                (lit.add if on else lit.discard)(msg.note)
                out.send(mido.Message("note_on", channel=0, note=msg.note, velocity=21 if on else 0))
    except KeyboardInterrupt:
        pass
    finally:
        for n in lit:
            out.send(mido.Message("note_on", channel=0, note=n, velocity=0))
