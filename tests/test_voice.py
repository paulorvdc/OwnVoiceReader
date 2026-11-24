"""Tests for the voice layer (M1). Skips cleanly when no TTS engine is present
so the suite stays green in a minimal environment."""

from __future__ import annotations

import io
import wave

import pytest

from ownvoicereader.voice import (
    build_tts_engine,
    get_style,
    style_names,
    STYLES,
)
from ownvoicereader.voice.tts_engine import wrap_pcm16_le


def test_wrap_pcm16_le_header():
    pcm = (b"\x00\x01" * 1000)  # 1000 16-bit mono samples
    wav = wrap_pcm16_le(pcm, sample_rate=22050, channels=1, bits=16)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[36:40] == b"data"
    assert len(wav) == 44 + len(pcm)
    # It must parse as a valid WAV.
    w = wave.open(io.BytesIO(wav))
    assert w.getnchannels() == 1
    assert w.getframerate() == 22050
    assert w.getnframes() == 1000


def test_styles_present():
    names = style_names()
    for s in ("academic-calm", "quick-scan", "narrative", "lecture"):
        assert s in names
    assert get_style("quick-scan").name == "quick-scan"
    # Unknown style falls back to the default.
    assert get_style("does-not-exist").name == "academic-calm"
    assert "length_scale" in STYLES["academic-calm"].to_dict()


def test_build_engine_or_none():
    eng = build_tts_engine()
    # Either we have a working engine, or a graceful None. Both are valid.
    assert eng is None or hasattr(eng, "synthesize")
    if eng is not None:
        assert eng.available in (True, False)


def test_piper_smoke_if_available():
    import asyncio

    eng = build_tts_engine()
    if eng is None:
        pytest.skip("no local TTS engine available")
    if eng.name != "piper":
        pytest.skip("piper not the active engine")
    wav = asyncio.run(eng.synthesize("Hello. This is a smoke test."))
    assert wav and wav[:4] == b"RIFF"
