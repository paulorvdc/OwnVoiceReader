"""Voice layer: TTS engine, reader styles, and audio playback."""

from .styles import Style, STYLES, get_style, style_names, DEFAULT_STYLE
from .tts_engine import (
    TtsEngine,
    PiperEngine,
    EspeakEngine,
    TtsUnavailable,
    build_tts_engine,
    locate_voice_dir,
    locate_piper_binary,
    ensure_default_voice,
    DEFAULT_VOICE,
)
from .player import Player, SystemPlayer, NullPlayer, build_player

__all__ = [
    "Style",
    "STYLES",
    "get_style",
    "style_names",
    "DEFAULT_STYLE",
    "TtsEngine",
    "PiperEngine",
    "EspeakEngine",
    "TtsUnavailable",
    "build_tts_engine",
    "locate_voice_dir",
    "locate_piper_binary",
    "ensure_default_voice",
    "DEFAULT_VOICE",
    "Player",
    "SystemPlayer",
    "NullPlayer",
    "build_player",
]
