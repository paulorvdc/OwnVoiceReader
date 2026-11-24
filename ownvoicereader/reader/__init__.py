"""Reader layer: the "right-moment" reading engine.

M1 wires the text path end to end via :class:`Orchestrator` (cues + playback
controls). M2 adds figure/table references and the EXPLAIN timeline; M5 adds
section detection and scoped selection.
"""

from .orchestrator import Orchestrator, Cue, build_cues

__all__ = ["Orchestrator", "Cue", "build_cues"]
