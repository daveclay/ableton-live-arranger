# Ableton Live Arranger — Spec (draft)

## Goal
Rearrange a song in Live's Arrangement View by **section** (verse, chorus, ...)
with one action, driven from an Akai APC40 mkII. Today, moving a section means
manually opening a gap, moving the clips, and closing the hole — and Live's
default behavior overwrites whatever is at the destination.

## Core rule
**Never overwrite, never leave gaps.** The song is an ordered list of sections;
operations change the order/contents of that list and the timeline reflows.

## Concepts
- **Section** — a named time range, from one Arrangement locator to the next.
  The locator's name is the section's name. The last section runs to the song end
  (end of last clip, or a terminating locator — TBD).
- **Song** — the ordered list of sections.
- **Picked tracks** — move/copy apply only to the tracks you've picked (e.g.
  drums + bass + guitar). Unpicked tracks (e.g. an ambient soundscape) are left
  exactly as they are. The picked set persists between operations until changed.
- Gaps between clips inside a section are preserved exactly.
- **Locators follow the picked tracks**: they describe the arrangement being
  edited, so they move/shift with it. Unpicked tracks may then no longer line
  up with the locators — that's expected.

## Operations
### Move section
`A B C D` → move B after C → `A C B D`
- Only the region from the start of B to the end of C is rewritten; everything
  outside is untouched.
- Locators move with their sections.

### Copy section
`A B C D` → copy B after C → `A B C B D`
- Everything after the insertion point (on picked tracks) shifts later by the
  length of B. Unpicked tracks don't shift, so the arranged tracks get longer.
- A new locator is created for the copy (e.g. "chorus 2" — naming TBD).

### Later / maybe
- Delete section (close the gap).
- Build a section from a subset of tracks ("track picking", see Hardware).
- Rename / split sections.

## Clip handling
- Clips fully inside a section move with it.
- Clips that **cross a section boundary** are split at the boundary; each part
  goes with its own section.
- Clip-level automation/envelopes travel with clips.

## Hardware: APC40 mkII
The APC isn't used by Live (Push 2 handles normal control), so this tool
owns the APC completely.
- Pad grid: show the sections in order, one pad per section; press a source pad, then a target
  position, to move/copy (a modifier button chooses copy vs move).
- Track picking (for future subset operations): a button enters pick mode, the
  Cue Level encoder scrolls a cursor (Live's selected track follows), pads/buttons
  toggle tracks; picked tracks shown lit on the grid.
- Exact button mapping: TBD.

### APC40 mkII MIDI map (measured, Ableton Live mode via SysEx 0x41)
Channels below are 0-based (mido). Buttons send note_on 127 / note_off.
| Control | Message |
|---|---|
| Clip pads 8×5 | note 0–39, ch 0 (0 = bottom-left row) |
| Track select (8) | note 51, ch = track 0–7 |
| Clip stop (8) | note 52, ch = track 0–7 |
| Scene launch (5) | note 82–86, top → bottom |
| Shift | note 98 (press + release, so holdable) |
| Bank ▲ ▼ ◄ ► | note 94, 95, 97, 96 |
| Device-section buttons | note 58–65 |
| Cue Level encoder | CC 47, relative: 1–63 = +n, 65–127 = −(128−n) |
| Top 8 knobs | CC 48–55, absolute |
| Device 8 knobs | CC 16–23, absolute (CC 16 on this unit jitters — don't use) |
| Faders | CC 7 ch = track; master CC 14; crossfader CC 15 |

Live MIDI prefs: APC40 mkII ports must have Track/Sync/Remote off and no
Control Surface slot using them.

## Architecture
- **External Python 3 process.**
  - Reads/writes the APC via MIDI (`mido` + `python-rtmidi`), including LED feedback.
  - Controls Live via OSC through **AbletonOSC** (a Remote Script in a free
    Control Surface slot; Push 2 keeps its own slot). Will extend AbletonOSC if
    needed LOM calls aren't exposed.
- Live MIDI prefs: APC40 control surface = None, its Track/Remote inputs off.
- Arranging logic (section math, reflow planning) is pure Python and unit-tested
  without Live: it produces a plan of clip operations which a thin adapter
  executes against Live.

## Spike results (Live 12.4.16, 2026-11-01)
- Arrangement clip extent = `loop_start`..`loop_end` (even when not looping);
  `start_marker` follows `loop_start`.
- **Trim right**: set `loop_end` → `end_time` shrinks, `start_time` fixed. ✅
- **Trim left**: set `loop_start` → `start_time` stays fixed, content shifts,
  `end_time` shrinks. ✅  So "piece of clip X from offset a..b placed at T" =
  duplicate, set loop_start/loop_end, (re)place.
- `Track.duplicate_clip_to_arrangement` **overwrites** whatever is under the
  full clip length at the destination → trim copies in a **scratch area past
  the song end** first, then duplicate the trimmed piece into place, delete scratch.
- `Track.delete_clip`, `Track.create_audio_clip`, `Song.begin_undo_step/end_undo_step`
  are available.
- **No track-level arrangement automation API.** Automation won't move via the LOM.
- Locator time not settable (`CuePoint.time` has no setter); only
  `set_or_delete_cue` at the playhead. Setting `current_song_time` takes effect
  on a *later* tick, so locator edits run as a multi-tick job.
- Consequence: an action = several Live undo steps (clip edits are one step,
  each locator edit another). The tool's own **undo** presses Live's undo until
  the set matches the pre-action snapshot, and refuses if the set changed since.
- Locator edits require the transport to be stopped.
- Verified end to end on the test set: move B after C (104 clip ops) and copy
  C after D on the drum group (135 ops) both match the plan exactly and undo back
  to the original.
- Still to test: unwarped audio clips (marker units), MIDI clips, undo grouping,
  clip fades at cut points.

## Risks — test first (spikes)
1. **Trimming/splitting clips at boundaries via the LOM.** `duplicate_clip_to_arrangement`
   exists; reliably trimming a clip's left edge is uncertain. Fallback: Live 12
   `create_audio_clip(file_path, position)` + set markers — may lose warp/gain settings.
2. **Arrangement automation** (track lanes, not clip envelopes): LOM access is
   limited; it may not move with sections.
3. **Locators**: cue point time may be read-only; may need delete + recreate.
4. **Tempo / time-signature changes** mid-song: ignored initially (assume constant).
5. **Undo**: one action should ideally be one undo step in Live (check feasibility).

## Open questions
- How is the song end defined for the last section?
- Naming for copied sections.
- APC button mapping.
