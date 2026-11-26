"""Reader layer: the "right-moment" reading engine.

M1 wires the text path end to end via :class:`Orchestrator` (cues + playback
controls). M2 adds figure/table references and the EXPLAIN timeline; M1.5 adds
section-aware navigation (jumping between sections + spoken section names).
"""

from .orchestrator import Orchestrator, Cue, build_cues
from .sections import (
    Section,
    detect_sections,
    section_for_block,
    is_heading,
    _body_font_size,
)

__all__ = [
    "Orchestrator",
    "Cue",
    "build_cues",
    "Section",
    "detect_sections",
    "section_for_block",
    "is_heading",
    "_body_font_size",
]
