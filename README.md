# Ableton Live Arranger

Move and copy song sections in Live 12's Arrangement View, on picked tracks
only, without ever overwriting or leaving gaps. It runs from the command line
or from an Akai APC40 mkII. See [SPEC.md](SPEC.md) for the design.

A **section** runs from one locator to the next and takes the locator's name.

## Every session

1. Turn on the APC40 mkII (on a data USB cable) and the audio interface.
2. Open Live and your set. LiveArranger stays selected as a Control Surface
   from last time, so there's nothing to do in Live's settings.
3. Start the APC controller. Either double-click `scripts/apc.command` in
   Finder, or run:

       cd ~/code/ableton-live-arranger
       .venv/bin/python -m arranger.apc

   Leave that window open while you work. You'll see
   `APC ready — N sections`, and the pads light up, one per section.
4. When you're done, press Ctrl+C or close the window. The APC goes dark.

Notes:
- Starting the controller before Live is fine; it waits for Live.
- If Live restarts or you reopen the set, the controller reconnects on its own.
- **Lit but not responding?** The controller isn't running (or it was stopped
  without Ctrl+C). Start it again.
- **Dark?** The controller is waiting for Live, or isn't running.
- Picks are forgotten when the controller restarts; pick the tracks again.

For the controls, see [APC40 mkII](#apc40-mkii) below.

## Setup (once)

1. Install the Remote Script into Live's User Library:

       scripts/install_remote_script.sh

   It copies `remote_script/LiveArranger` to
   `~/Google Drive/Music/Abelton/User Library/User Library/Remote Scripts/`.
   Set `LIVE_REMOTE_SCRIPTS=/path/to/Remote\ Scripts` to install it somewhere else.

2. In Live, open **Settings → Link, Tempo & MIDI**. Pick **LiveArranger** in an
   empty Control Surface slot and set its Input and Output to **None**.
   - LiveArranger listens on `127.0.0.1:11020`.
   - Its log is `logs/livearranger.log` inside the installed script folder.

3. For the APC40 mkII:
   - Don't give the APC to any Control Surface slot (not the stock APC
     script, not AbletonOSC).
   - Turn off Track, Sync and Remote for the APC's MIDI ports. The arranger
     talks to the APC directly.
   - The APC needs a data USB cable; a charge-only cable won't show up.

4. Python environment for the APC controller (system `python3` is enough for
   the CLI and the tests):

       python3 -m venv .venv
       .venv/bin/pip install mido python-rtmidi

### After changing code

| What changed | What to do |
|---|---|
| `remote_script/LiveArranger/commands.py` | Run `scripts/install_remote_script.sh`; it hot-reloads the commands |
| `manager.py` or `server.py` | Run the install script, then restart Live |
| `arranger/*` | Restart the APC controller; the CLI needs nothing |

## Unit tests (no Live needed)

    python3 -m unittest discover -s tests

- `test_planner.py` covers every move and copy on a snapshot of the test set,
  checked in a simulator that refuses any overwrite.
- `test_apc.py` covers the APC controller against a fake Live.

## Testing in Live

Use the throwaway test set, never a real song:

    ~/Google Drive/art/after/Music/Arranger Test/Arranger Test Project/Arranger Test.als

It is 4/4 at 88 BPM, with locators A–F and a `drm grp` group
(kck, drm nze, snr, hihat, nze). `nze` has Device On automation, so you
should see the warning that automation won't move.

**Stop the transport before any edit.**

### Command line

    python3 -m arranger tracks
    python3 -m arranger sections --tracks "drm grp"
    python3 -m arranger move B --after C --tracks "drm grp" --dry-run
    python3 -m arranger move B --after C --tracks "drm grp"
    python3 -m arranger copy B --before E --tracks kck,snr --as "B again"
    python3 -m arranger undo

- `--tracks` takes names or indices, separated by commas. A group name picks
  all its tracks; `all` picks every track with clips.
- `--dry-run` prints the plan and changes nothing.
- `-y` skips the confirmation.
- Every edit is checked in the simulator first. Afterwards the set is read
  back from Live and compared with the plan.

What to check after each edit:
- The picked tracks are in the new order, with nothing overwritten and no new gaps.
- The gaps between clips inside each section are unchanged.
- The unpicked tracks haven't moved.
- The locators moved with their sections. A copy gets a new locator ("B 2" by default).
- `python3 -m arranger undo` restores the set exactly. It takes several Live
  undo steps, because each locator edit is its own step.
- Undo refuses if you changed the set after the edit. In that case use Cmd+Z in Live.

### APC40 mkII

With Live open and LiveArranger selected:

    .venv/bin/python -m arranger.apc

It prints `APC ready — N sections`. Stop it with Ctrl+C, which also turns
the LEDs off. Messages appear in the terminal and in Live's status bar.

| Control | Action |
|---|---|
| Track select 1–8 | Select that track in Live, and pick or unpick it (a group picks all its tracks). Lit = picked |
| Bank ◄ / ► | Previous / next 8 tracks |
| Cue Level knob | Move the track cursor (shown on the clip-stop lights; Live's selected track follows) |
| Tempo knob | Move the playhead by bars, snapping to the bar (Shift: by 16th notes) |
| Device On/Off | Pick or unpick the cursor's track |
| Shift + Device On/Off | Clear all picks |
| Clip pads | One pad per section in song order, 8 per row starting at the top-left |
| Device ◄ | Undo the last move or copy |
| Device Lock | Add a locator at the playhead (Shift: remove the one there) |

To use the pads:
- Tap a section to pick it up. It turns white.
- Tap another section to **move** the picked-up one in front of it.
- Hold **Shift** while tapping the second pad to **copy** instead.
- Tap the dim pad after the last section to put it at the end.
- Tap the white pad again to cancel.

A quick test:
1. Pick `drm grp` with track select 1. Track selects 2–6 light up.
2. Tap B (pad 2), then D (pad 4). Expect `done: move B before D` and the
   section order A C B D E F.
3. Press Device ◄. Expect `undone (3 steps)` and the set as it was.
4. Shift + tap to copy a section to the end. Then undo it (4 steps).

### MIDI helpers

    .venv/bin/python scripts/midi_listen.py 20        # print what the APC sends (sends nothing)
    .venv/bin/python scripts/apc_monitor.py 20        # Live mode + green pads while you press things

The full measured APC40 mkII MIDI map is in SPEC.md.

## Not handled yet

- Looped clips and unwarped clips: the plan refuses them.
- Track automation doesn't move; you get a warning instead.
- Not tested yet: MIDI clips, fades at cut points, tempo changes.
