"""Command handlers. Hot-reloadable: send {"cmd": "reload"} after editing.

Times are in beats (Live's native arrangement unit).
"""

import math

import Live

# Only fixed, named commands are exposed — no arbitrary code execution.


def dispatch(manager, cmd, args):
    fn = COMMANDS.get(cmd)
    if fn is None:
        raise KeyError("unknown command %r (have: %s)" % (cmd, ", ".join(sorted(COMMANDS))))
    return fn(manager, manager.song, **args)


def ping(manager, song):
    return "pong"


def _public(obj):
    return sorted(n for n in dir(obj) if not n.startswith("_"))


def probe(manager, song):
    """Read-only: list API members relevant to arranging."""
    return {
        "Song": _public(Live.Song.Song),
        "Track": _public(Live.Track.Track),
        "Clip": _public(Live.Clip.Clip),
        "CuePoint": _public(Live.Song.CuePoint),
        "Song.View": _public(Live.Song.Song.View),
        "Application.View": _public(Live.Application.Application.View),
    }


def _clip_info(clip):
    info = {
        "name": clip.name,
        "start_time": clip.start_time,
        "end_time": clip.end_time,
        "is_audio": clip.is_audio_clip,
        "looping": clip.looping,
        "start_marker": clip.start_marker,
        "end_marker": clip.end_marker,
        "loop_start": clip.loop_start,
        "loop_end": clip.loop_end,
    }
    if clip.is_audio_clip:
        info.update(file_path=clip.file_path, warping=clip.warping, gain=clip.gain)
    return info


def state(manager, song):
    """Read-only snapshot of the arrangement."""
    tracks = []
    for i, t in enumerate(song.tracks):
        tracks.append({
            "index": i,
            "name": t.name,
            "is_group": t.is_foldable,
            "group": t.group_track.name if t.is_grouped else None,
            "clips": [] if t.is_foldable else [_clip_info(c) for c in t.arrangement_clips],
        })
    cues = sorted(({"name": c.name, "time": c.time} for c in song.cue_points), key=lambda c: c["time"])
    sel = song.view.selected_track
    selected = next((i for i, t in enumerate(song.tracks) if t == sel), None)
    return {
        "selected_track": selected,
        "is_playing": song.is_playing,
        "song_time": song.current_song_time,
        "start_time": song.start_time,
        "tempo": song.tempo,
        "signature": [song.signature_numerator, song.signature_denominator],
        "song_length": song.song_length,
        "cue_points": cues,
        "tracks": tracks,
    }


def _find_clip(song, track, start_time, tol=1e-4):
    for c in song.tracks[track].arrangement_clips:
        if abs(c.start_time - start_time) < tol:
            return c
    raise LookupError("no arrangement clip at %s on track %d" % (start_time, track))


def duplicate_clip(manager, song, track, start_time, dest_time):
    """Copy the arrangement clip at start_time to dest_time on the same track."""
    t = song.tracks[track]
    new = t.duplicate_clip_to_arrangement(_find_clip(song, track, start_time), dest_time)
    return _clip_info(new) if new is not None else _clip_info(_find_clip(song, track, dest_time))


SETTABLE = ("start_marker", "end_marker", "loop_start", "loop_end", "looping")


def set_clip(manager, song, track, start_time, props):
    """Set marker/loop properties (in the given order) on an arrangement clip."""
    clip = _find_clip(song, track, start_time)
    for key, value in props:
        if key not in SETTABLE:
            raise ValueError("can't set %r" % key)
        setattr(clip, key, value)
    return _clip_info(clip)


def delete_clip(manager, song, track, start_time):
    song.tracks[track].delete_clip(_find_clip(song, track, start_time))
    return "deleted"


def clips(manager, song, track):
    return [_clip_info(c) for c in song.tracks[track].arrangement_clips]


TOL = 1e-3


def _expect(clip, start, end, what):
    if abs(clip.start_time - start) > TOL or abs(clip.end_time - end) > TOL:
        raise RuntimeError("%s: expected %.4f-%.4f, Live has %.4f-%.4f"
                           % (what, start, end, clip.start_time, clip.end_time))


def _cue_at(song, t):
    for c in song.cue_points:
        if abs(c.time - t) < TOL:
            return c
    return None


def _run_op(song, op):
    track = song.tracks[op["track"]]
    kind = op["op"]
    if kind == "stage":
        src = _find_clip(song, op["track"], op["src"])
        track.duplicate_clip_to_arrangement(src, op["at"])
        clip = _find_clip(song, op["track"], op["at"])
        clip.loop_end = op["loop_end"]      # right edge first so loop_start < loop_end always holds
        clip.loop_start = op["loop_start"]
        _expect(clip, op["at"], op["at"] + op["loop_end"] - op["loop_start"], "stage on %s" % track.name)
    elif kind == "place":
        src = _find_clip(song, op["track"], op["src"])
        length = src.end_time - src.start_time
        track.duplicate_clip_to_arrangement(src, op["dest"])
        _expect(_find_clip(song, op["track"], op["dest"]), op["dest"], op["dest"] + length,
                "place on %s" % track.name)
    elif kind == "delete":
        track.delete_clip(_find_clip(song, op["track"], op["at"]))
    else:
        raise ValueError("unknown op %r" % kind)


# ---- jobs: work that must span several Live ticks -------------------------
# Setting song.current_song_time only takes effect on a later tick, and
# locators can only be toggled at the playhead, so locator edits run as a
# generator advanced once per tick by on_tick(). A generator `yield`s to wait.

MAX_WAIT_TICKS = 30


def _jobs(manager):
    if not hasattr(manager, "jobs"):
        manager.jobs = {}
        manager.next_job = 1
    return manager.jobs


def on_tick(manager):
    for job_id, job in list(_jobs(manager).items()):
        if job["status"] != "running":
            continue
        try:
            next(job["gen"])
        except StopIteration as stop:
            job.update(status="done", result=stop.value)
        except Exception as e:
            import traceback
            job.update(status="failed", error="%s: %s" % (type(e).__name__, e), traceback=traceback.format_exc())


def _start_job(manager, gen):
    jobs = _jobs(manager)
    job_id = manager.next_job
    manager.next_job += 1
    jobs[job_id] = {"status": "running", "gen": gen}
    for old in [k for k in jobs if k < job_id - 20]:
        del jobs[old]
    return job_id


def job_status(manager, song, job):
    j = _jobs(manager).get(job)
    if j is None:
        raise LookupError("no job %r" % job)
    return {k: v for k, v in j.items() if k != "gen"}


def _seek(song, t):
    """Generator: move the playhead to t and wait until Live has applied it."""
    song.current_song_time = t
    for _ in range(MAX_WAIT_TICKS):
        yield
        if abs(song.current_song_time - t) < TOL:
            return
    raise RuntimeError("playhead didn't reach %.4f" % t)


def _restore_playhead(song, t):
    try:
        song.current_song_time = min(t, song.song_length)
    except RuntimeError:
        pass  # cosmetic only; never fail an edit over the playhead


def _snapshot(song):
    """Comparable fingerprint of all arrangement clips and locators."""
    tracks = tuple(
        () if t.is_foldable else tuple(
            (round(c.start_time, 3), round(c.end_time, 3), round(c.loop_start, 3)) for c in t.arrangement_clips)
        for t in song.tracks)
    cues = tuple(sorted((round(c.time, 3), c.name) for c in song.cue_points))
    return tracks, cues


MAX_UNDO_STEPS = 20


def _undo_until(song, target):
    """Generator: press undo until the set matches `target`. Locator edits made
    over several ticks land in separate Live undo steps, so one press isn't enough."""
    for n in range(1, MAX_UNDO_STEPS + 1):
        if not song.can_undo:
            break
        song.undo()
        yield
        if _snapshot(song) == target:
            return n
    raise RuntimeError("couldn't get back to the previous state by undoing; check the set (Cmd+Z / Cmd+Shift+Z)")


def _history(manager):
    if not hasattr(manager, "history"):
        manager.history = []
    return manager.history


def _apply_job(manager, song, ops, cue_deletes, cue_creates):
    playhead = song.current_song_time
    before = _snapshot(song)
    song.begin_undo_step()
    try:
        for op in ops:  # clip edits are synchronous: all in this tick, one undo step
            _run_op(song, op)
        song.end_undo_step()
        for t in cue_deletes:
            if _cue_at(song, t) is None:
                raise RuntimeError("no locator at %.4f to remove" % t)
            yield from _seek(song, t)
            song.set_or_delete_cue()
            yield
            if _cue_at(song, t) is not None:
                raise RuntimeError("couldn't remove locator at %.4f" % t)
        for name, t in cue_creates:
            if _cue_at(song, t) is None:
                yield from _seek(song, t)
                song.set_or_delete_cue()
                yield
            cue = _cue_at(song, t)
            if cue is None:
                raise RuntimeError("couldn't create locator at %.4f" % t)
            cue.name = name
    except Exception:
        song.end_undo_step()  # harmless if already ended
        if _snapshot(song) != before:
            yield from _undo_until(song, before)
        _restore_playhead(song, playhead)
        raise
    _restore_playhead(song, playhead)
    _history(manager).append({"before": before, "after": _snapshot(song)})
    del _history(manager)[:-20]
    return {"ops": len(ops), "cues_removed": len(cue_deletes), "cues_created": len(cue_creates)}


def apply_plan(manager, song, ops, cue_deletes, cue_creates):
    """Start applying a planner Plan. Returns a job id; poll job_status."""
    if (cue_deletes or cue_creates) and song.is_playing:
        raise RuntimeError("stop the transport first (locators are edited via the playhead)")
    job_id = _start_job(manager, _apply_job(manager, song, ops, cue_deletes, cue_creates))
    on_tick(manager)  # run the synchronous clip edits right away
    return {"job": job_id}


def _arrange_undo_job(manager, song):
    hist = _history(manager)
    if not hist:
        raise RuntimeError("no arranger action to undo")
    last = hist[-1]
    now = _snapshot(song)
    if now != last["after"]:
        changed = [song.tracks[i].name for i, (a, b) in enumerate(zip(now[0], last["after"][0])) if a != b]
        if now[1] != last["after"][1]:
            changed.append("locators")
        raise RuntimeError("the set changed since the last arranger action (%s); use Cmd+Z in Live instead"
                           % ", ".join(changed or ["track list"]))
    playhead = song.current_song_time
    steps = yield from _undo_until(song, last["before"])
    hist.pop()
    _restore_playhead(song, playhead)
    return {"undo_steps": steps}


def arrange_undo(manager, song):
    """Undo the last arranger action (all of its Live undo steps), only if nothing else changed since."""
    job_id = _start_job(manager, _arrange_undo_job(manager, song))
    on_tick(manager)
    return {"job": job_id}


def undo(manager, song):
    if not song.can_undo:
        raise RuntimeError("nothing to undo")
    song.undo()
    return "undone"


def _params(track):
    yield track.mixer_device.volume
    yield track.mixer_device.panning
    for s in track.mixer_device.sends:
        yield s
    for d in track.devices:
        for p in d.parameters:
            yield p


def automation_report(manager, song, tracks):
    """Automated parameters on the given tracks (their arrangement automation won't move)."""
    out = []
    for i in tracks:
        t = song.tracks[i]
        names = [p.name for p in _params(t) if getattr(p, "automation_state", 0)]
        if names:
            out.append([t.name, names])
    return out


def diag_cues(manager, song):
    """Diagnostics: is current_song_time applied immediately? Is CuePoint.time writable?
    Restores everything it touches."""
    out = {"cue_members": _public(song.cue_points[0]) if song.cue_points else []}
    before = song.current_song_time
    song.current_song_time = before + 4.0
    out["song_time_immediate"] = abs(song.current_song_time - (before + 4.0)) < TOL
    song.current_song_time = before
    if song.cue_points:
        cue = song.cue_points[-1]
        t0 = cue.time
        try:
            cue.time = t0 + 4.0
            out["cue_time_writable"] = abs(cue.time - (t0 + 4.0)) < TOL
            cue.time = t0
        except Exception as e:
            out["cue_time_writable"] = "%s: %s" % (type(e).__name__, e)
    return out


def select_track(manager, song, track):
    song.view.selected_track = song.tracks[track]
    return track


def nudge_playhead(manager, song, steps, unit="bar"):
    """Move the playhead by `steps` bars, beats or sixteenths, snapping to that grid.

    current_song_time only updates on a later tick, so fast knob turns build on
    the last target we set rather than the stale reading."""
    beats = song.signature_numerator * 4.0 / song.signature_denominator
    beat = 4.0 / song.signature_denominator
    size = {"bar": beats, "beat": beat, "sixteenth": 0.25}[unit]
    base = song.current_song_time
    target = getattr(manager, "_playhead_target", None)
    if target is not None and abs(base - target) > TOL:
        base = target
    grid = base / size
    # Snap to the grid in the direction of travel, then step.
    cell = math.floor(grid + TOL) if steps > 0 else math.ceil(grid - TOL)
    new = max(0.0, min((cell + steps) * size, song.song_length))
    manager._playhead_target = new
    song.current_song_time = new
    # Play starts from the start marker, not the playhead; move it too.
    song.start_time = new
    return new


def _locator_job(manager, song, add):
    t = song.current_song_time
    target = getattr(manager, "_playhead_target", None)
    if not song.is_playing and target is not None and abs(t - target) > TOL:
        yield from _seek(song, target)  # a knob move hasn't landed yet
        t = target
    cue = _cue_at(song, t)
    if add and cue is not None:
        return {"done": False, "name": cue.name, "time": t}
    if not add and cue is None:
        return {"done": False, "name": None, "time": t}
    song.set_or_delete_cue()  # toggles a locator at the playhead
    yield
    cue = _cue_at(song, t)
    if (cue is None) == add:
        raise RuntimeError("couldn't %s the locator at %.4f" % ("add" if add else "remove", t))
    return {"done": True, "name": cue.name if cue else None, "time": t}


def locator_at_playhead(manager, song, add=True):
    """Add (or remove) a locator at the playhead. Never toggles the other way."""
    return {"job": _start_job(manager, _locator_job(manager, song, add))}


def rename_locator(manager, song, time, name):
    cue = _cue_at(song, time)
    if cue is None:
        raise LookupError("no locator at %.4f" % time)
    cue.name = str(name)
    return cue.name


def message(manager, song, text):
    """Show text in Live's status bar."""
    manager.show_message(str(text)[:200])
    return "ok"


COMMANDS = {
    "select_track": select_track,
    "message": message,
    "nudge_playhead": nudge_playhead,
    "locator_at_playhead": locator_at_playhead,
    "rename_locator": rename_locator,
    "diag_cues": diag_cues,
    "apply_plan": apply_plan,
    "job_status": job_status,
    "arrange_undo": arrange_undo,
    "undo": undo,
    "automation_report": automation_report,
    "ping": ping,
    "probe": probe,
    "state": state,
    "clips": clips,
    "duplicate_clip": duplicate_clip,
    "set_clip": set_clip,
    "delete_clip": delete_clip,
}
