"""voice/styles.py — the four reader style presets (A8).

A "style" is a named mapping of synthesis parameters. For the Piper engine it
maps to ``--length-scale`` (pace), ``--noise-scale`` (expressiveness) and a
sentence pause. The same preset names are understood by every engine; an engine
that doesn't support a parameter simply ignores it. This keeps style switching
(M4) engine-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Style:
    """A named reader style."""

    name: str
    description: str
    # Piper / general synthesis knobs (None => engine default).
    length_scale: Optional[float] = None   # >1 slower, <1 faster
    noise_scale: Optional[float] = None     # higher => more natural variation
    sentence_silence: Optional[float] = None  # seconds of pause between sentences
    volume: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "length_scale": self.length_scale,
            "noise_scale": self.noise_scale,
            "sentence_silence": self.sentence_silence,
            "volume": self.volume,
        }


# A8: four reader styles.
STYLES: dict[str, Style] = {
    "academic-calm": Style(
        "academic-calm",
        "Steady, measured pace for careful study — the default.",
        length_scale=1.05, noise_scale=0.55, sentence_silence=0.35,
    ),
    "quick-scan": Style(
        "quick-scan",
        "Faster pace for skimming and getting the gist.",
        length_scale=1.35, noise_scale=0.5, sentence_silence=0.25,
    ),
    "narrative": Style(
        "narrative",
        "Warm, expressive reading — like a storyteller.",
        length_scale=0.95, noise_scale=0.8, sentence_silence=0.5,
    ),
    "lecture": Style(
        "lecture",
        "Even, confident lecture pace with clear pauses.",
        length_scale=1.0, noise_scale=0.6, sentence_silence=0.45,
    ),
}

DEFAULT_STYLE = "academic-calm"


def get_style(name: Optional[str]) -> Style:
    """Resolve a style by name, falling back to the default."""
    if name and name in STYLES:
        return STYLES[name]
    return STYLES[DEFAULT_STYLE]


def style_names() -> list[str]:
    return list(STYLES.keys())
