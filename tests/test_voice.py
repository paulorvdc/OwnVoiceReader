"""Tests for the voice layer (M1). Skips cleanly when no TTS engine is present
so the suite stays green in a minimal environment."""

from __future__ import annotations

import io
import wave

import pytest

from ownvoicereader.voice import (
    build_tts_engine,
    ensure_default_voice,
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


# --------------------------------------------------------------------------- #
# M6 Phase 2: the bundled default voice, copied on first run
# --------------------------------------------------------------------------- #
def _make_bundled_voice(bundled_dir, voice="en_US-lessac-medium"):
    """Lay down a fake bundled voice: a tiny .onnx + its .onnx.json config."""
    bundled_dir.mkdir(parents=True, exist_ok=True)
    (bundled_dir / f"{voice}.onnx").write_bytes(b"FAKE-ONNX-MODEL-BYTES")
    (bundled_dir / f"{voice}.onnx.json").write_text('{"sampling_rate": 22050}')


def test_ensure_default_voice_copies_on_first_run(tmp_path):
    """A bundled voice is copied into a (missing) user voice dir on first run."""
    bundled = tmp_path / "bundled"
    _make_bundled_voice(bundled)
    target = tmp_path / "voices"  # does not exist yet
    out = ensure_default_voice(voice_dir=target, bundled_dir=bundled)
    assert out == target / "en_US-lessac-medium.onnx"
    assert out.is_file()
    assert out.read_bytes() == b"FAKE-ONNX-MODEL-BYTES"
    # the config travels with the model
    assert (target / "en_US-lessac-medium.onnx.json").is_file()


def test_ensure_default_voice_is_idempotent(tmp_path):
    """A second run with the same voice present is a no-op (matched by name + size)."""
    bundled = tmp_path / "bundled"
    _make_bundled_voice(bundled)
    target = tmp_path / "voices"
    first = ensure_default_voice(voice_dir=target, bundled_dir=bundled)
    assert first is not None
    second = ensure_default_voice(voice_dir=target, bundled_dir=bundled)
    assert second == first
    # exactly one copy, never duplicated
    assert list(target.glob("*.onnx")) == [target / "en_US-lessac-medium.onnx"]


def test_ensure_default_voice_no_bundled_voice_is_a_noop(tmp_path):
    """With no bundled voice there is nothing to copy: None, and nothing created."""
    target = tmp_path / "voices"
    out = ensure_default_voice(voice_dir=target, bundled_dir=tmp_path / "empty-bundled")
    assert out is None
    assert not target.exists()  # a missing target is not created for nothing


def test_ensure_default_voice_degrades_on_bad_target(tmp_path):
    """A target that can't be written is logged, never fatal (degrade, never abort)."""
    bundled = tmp_path / "bundled"
    _make_bundled_voice(bundled)
    a_file = tmp_path / "a-file"      # voice_dir points at a *file* -> mkdir raises
    a_file.write_text("x")
    out = ensure_default_voice(voice_dir=a_file, bundled_dir=bundled)
    assert out is None


def test_build_tts_engine_ensures_default_voice(monkeypatch, tmp_path):
    """build_tts_engine ensures the bundled voice is present before picking an engine."""
    from ownvoicereader.voice import tts_engine

    calls = []
    monkeypatch.setattr(
        tts_engine, "ensure_default_voice",
        lambda *a, **k: calls.append((a, k)) or None,
    )
    tts_engine.build_tts_engine(voice_dir=tmp_path / "voices")
    assert calls, "build_tts_engine should call ensure_default_voice on every build"
