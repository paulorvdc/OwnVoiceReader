"""Core data shapes — the "interchangeable contract" (BUILD_PLAN.md section 4).

Text extraction and OCR both feed the *same* :class:`Block` shape, so the
reader pipeline never branches on whether a block came from a text layer or
from OCR. These shapes are the contract shared by every module.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class BlockKind(str, Enum):
    """What kind of content a block holds."""

    TEXT = "text"
    FIGURE = "figure"
    TABLE = "table"


class BlockSource(str, Enum):
    """Where the block's content came from. The reader is origin-agnostic;
    this is recorded for debugging and for parity checks (M3)."""

    TEXT = "text"
    OCR = "ocr"


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BBox:
    """A page-space bounding box: x0,y0 = top-left; x1,y1 = bottom-right.

    Coordinates are in PDF user space (origin top-left after the standard
    PDF.js/PyMuPDF transform) and are used for figure/table cropping and for
    anchoring text-layer selection (M5).
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    @classmethod
    def from_tuple(cls, t: tuple[float, float, float, float]) -> "BBox":
        return cls(t[0], t[1], t[2], t[3])

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


# --------------------------------------------------------------------------- #
# The unified block
# --------------------------------------------------------------------------- #
@dataclass
class Block:
    """One unit of readable content: a run of text, a figure, or a table.

    This is the *single* shape that flows through reading-order, references,
    the timeline, and TTS. ``image`` is set only for figures (and optionally
    tables); ``table_grid`` is set only for tables; ``caption`` is the caption
    text for figures/tables (used by the no-vision fallback, A2).
    """

    kind: BlockKind
    text: str
    page: int
    bbox: Optional[BBox] = None
    source: BlockSource = BlockSource.TEXT
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    image: Optional[bytes] = None
    caption: Optional[str] = None
    table_grid: Optional[list[list[Any]]] = None
    # M1.5: typographic cues for section detection (pulled forward from M5).
    # Propagated from the block's dominant line by reading_order:
    #   font_size = the *largest* span size in the block (a heading stands out
    #               by being bigger than the ~9-10pt body)
    #   is_bold   = the boldness of the block's longest (main) line
    font_size: float = 0.0
    is_bold: bool = False

    # --- helpers --------------------------------------------------------- #
    @classmethod
    def text_block(
        cls,
        text: str,
        page: int,
        bbox: Optional[BBox] = None,
        source: BlockSource = BlockSource.TEXT,
        font_size: float = 0.0,
        is_bold: bool = False,
    ) -> "Block":
        return cls(kind=BlockKind.TEXT, text=text, page=page, bbox=bbox, source=source,
                   font_size=font_size, is_bold=is_bold)

    @classmethod
    def figure_block(
        cls,
        image: bytes,
        page: int,
        bbox: Optional[BBox] = None,
        caption: Optional[str] = None,
        source: BlockSource = BlockSource.TEXT,
    ) -> "Block":
        return cls(kind=BlockKind.FIGURE, text="", image=image, page=page, bbox=bbox,
                   caption=caption, source=source)

    @classmethod
    def table_block(
        cls,
        table_grid: list[list[Any]],
        page: int,
        bbox: Optional[BBox] = None,
        caption: Optional[str] = None,
        source: BlockSource = BlockSource.TEXT,
    ) -> "Block":
        return cls(kind=BlockKind.TABLE, text="", image=None, page=page, bbox=bbox,
                   caption=caption, table_grid=table_grid, source=source)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["source"] = self.source.value
        if self.bbox is not None:
            d["bbox"] = self.bbox.to_dict()
        # image bytes are not serialized to the browser; we expose a flag.
        d["has_image"] = self.image is not None
        d["image"] = None
        return d


# --------------------------------------------------------------------------- #
# Figure / table references (M2) — a "Fig. 3" / "Table 2(b)" mention in text
# --------------------------------------------------------------------------- #
@dataclass
class Reference:
    """A textual mention of a figure or table, e.g. ``Fig. 3`` / ``Table 2(b)``.

    ``target`` is the normalized label the reference points at (``"fig:3"``,
    ``"table:2"``, ``"table:2b"``). ``first_occurrence`` marks the mention that
    should trigger an EXPLAIN event by default (A7).
    """

    kind: BlockKind
    target: str
    raw: str
    page: int
    block_id: str
    first_occurrence: bool = False


# --------------------------------------------------------------------------- #
# Reading events (M1/M2) — the stream the orchestrator emits to the viewer
# --------------------------------------------------------------------------- #
class ReadingEventType(str, Enum):
    TEXT = "text"            # speak this chunk of prose
    EXPLAIN = "explain"      # speak an LLM explanation of a figure/table
    PAUSE = "pause"          # reader paused by user
    RESUME = "resume"
    STOP = "stop"
    SECTION = "section"      # entering a new detected section
    NOTICE = "notice"        # non-spkoken status (e.g. "LLM unavailable, skipping")


@dataclass
class ReadingEvent:
    """One item in the reading stream that the viewer consumes."""

    type: ReadingEventType
    text: str = ""
    block_id: Optional[str] = None
    page: Optional[int] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


# --------------------------------------------------------------------------- #
# The whole document
# --------------------------------------------------------------------------- #
@dataclass
class DocumentMetadata:
    """Metadata read from a PDF's info dictionary + page geometry."""

    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    creation_date: str = ""
    modified_date: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadingDocument:
    """Everything the reader needs about one PDF: identity, geometry, and blocks.

    In M0 this is populated with identity + metadata + page count only;
    ``blocks`` is filled in by M1/M2 (text, figures, tables, OCR).
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source_path: str = ""
    title: str = ""
    page_count: int = 0
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    blocks: list[Block] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    # Sparse/empty pages flagged by the text extractor (M1): pages whose text
    # layer is missing or trivial and which may need OCR (M3).
    sparse_pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "title": self.title,
            "page_count": self.page_count,
            "metadata": self.metadata.to_dict(),
            "block_count": len(self.blocks),
            "sparse_pages": self.sparse_pages,
            "created_at": self.created_at,
        }
