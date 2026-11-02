"""Arrangement data model. All times are in beats."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

EPS = 1e-3  # tolerance for comparing beat positions


@dataclass(frozen=True)
class Clip:
    track: int
    start: float
    end: float
    loop_start: float  # clip-content position shown at `start`
    name: str = ""
    looping: bool = False
    warping: bool = True
    is_audio: bool = True

    def content_at(self, t: float) -> float:
        """Clip-content position heard at timeline time t (warped, non-looping)."""
        return self.loop_start + (t - self.start)

    def overlaps(self, a: float, b: float) -> bool:
        return self.start < b - EPS and self.end > a + EPS


@dataclass
class Track:
    index: int
    name: str
    clips: List[Clip] = field(default_factory=list)
    is_group: bool = False
    group: Optional[str] = None


@dataclass(frozen=True)
class Cue:
    name: str
    time: float


@dataclass(frozen=True)
class Section:
    name: str
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class Arrangement:
    tracks: List[Track]
    cues: List[Cue]
    signature: tuple = (4, 4)
    tempo: float = 120.0

    @classmethod
    def from_state(cls, state: dict) -> "Arrangement":
        tracks = []
        for t in state["tracks"]:
            clips = [
                Clip(
                    track=t["index"], start=c["start_time"], end=c["end_time"],
                    loop_start=c["loop_start"], name=c["name"], looping=c["looping"],
                    warping=c.get("warping", True), is_audio=c["is_audio"],
                )
                for c in t["clips"]
            ]
            tracks.append(Track(t["index"], t["name"], clips, t["is_group"], t["group"]))
        cues = sorted((Cue(c["name"], c["time"]) for c in state["cue_points"]), key=lambda c: c.time)
        return cls(tracks, cues, tuple(state["signature"]), state["tempo"])

    @property
    def beats_per_bar(self) -> float:
        num, den = self.signature
        return num * 4.0 / den

    def content_end(self, track_indices) -> float:
        ends = [c.end for i in track_indices for c in self.tracks[i].clips]
        return max(ends, default=0.0)

    def sections(self, track_indices) -> List[Section]:
        """Sections run from each locator to the next; the last runs to the end
        of the picked tracks' content. Material before the first locator is not
        a section (it stays put)."""
        cues = self.cues
        if not cues:
            return []
        # Round the last section up to whole bars (from its locator) so moving it
        # doesn't knock everything after it off the bar grid.
        last, bar = cues[-1].time, self.beats_per_bar
        tail = max(self.content_end(track_indices) - last, 0.0)
        end = last + math.ceil(tail / bar - EPS) * bar
        out = []
        for i, cue in enumerate(cues):
            nxt = cues[i + 1].time if i + 1 < len(cues) else end
            if nxt - cue.time > EPS:
                out.append(Section(cue.name, cue.time, nxt))
        return out

    def bar_of(self, beats: float) -> float:
        return beats / self.beats_per_bar + 1
