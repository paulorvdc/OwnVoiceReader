"""Configuration for OwnVoiceReader.

M0 wires the minimum (server bind, browser, playback, flags) and sets up the
structure for M4's full layering:

    defaults  ->  ~/.ownvoicereader/config.yaml  ->  env (OWNVOICEREADER_*)  ->  CLI flags

For M0 the layers implemented are *defaults -> env -> CLI*; the YAML layer is
added in M4. API credentials (OPENAI_API_KEY, ELEVENLABS_API_KEY, OLLAMA_HOST,
…) are read **from the OS environment only** and are never written to disk
(A6) — this keeps secrets out of our hands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# A small allow-list of environment variables we *read* for provider access.
# These are passed through to the LLM/TTS clients untouched; we never persist
# them. Documented in the README so the user knows exactly what is read.
PROVIDER_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ELEVENLABS_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_HOST",
    "OLLAMA_MODELS",
)


def provider_env() -> dict[str, str]:
    """Return the subset of the OS environment relevant to providers.

    Read live at call time so a user can export a key in the same shell.
    """
    return {k: os.environ[k] for k in PROVIDER_ENV_VARS if k in os.environ}


@dataclass
class Config:
    """Resolved configuration. CLI flags take final precedence."""

    # --- document ------------------------------------------------------- #
    pdf_path: Optional[str] = None

    # --- server (A13: 127.0.0.1 only, fixed default port, local-only) ---- #
    host: str = "127.0.0.1"
    # A fixed, memorable default makes the URL stable and easy to open and to
    # reason about when debugging connections. 0 (or --port 0) means "pick a
    # free high port" — the old behaviour, still available.
    port: int = 6767
    open_browser: bool = True           # --no-browser disables
    local_only: bool = True            # forbid any cloud egress

    # --- playback (A9) -------------------------------------------------- #
    playback: str = "browser"          # "browser" (HTML5 <audio>) | "system"

    # --- reading behaviour (A7) ---------------------------------------- #
    director: bool = False             # LLM places explanations; else first-ref

    # --- headless / CI (A12) ------------------------------------------- #
    auto: bool = False                 # read start-to-finish without UI control

    # --- LLM / voice / style (M4 makes these live-switchable) ----------- #
    llm_provider: Optional[str] = None   # the local Ollama server (via the `ollama` lib); default provider is "ollama"
    llm_model: Optional[str] = None
    voice: Optional[str] = None
    style: Optional[str] = None        # academic-calm | quick-scan | narrative | lecture

    # --- resume checkpoint (A14) --------------------------------------- #
    checkpoint_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config, applying defaults, then overriding from OWNVOICEREADER_*."""
        cfg = cls()
        env = os.environ
        if v := env.get("OWNVOICEREADER_HOST"):
            cfg.host = v
        if v := env.get("OWNVOICEREADER_PORT"):
            try:
                cfg.port = int(v)
            except ValueError:
                pass
        if v := env.get("OWNVOICEREADER_LOCAL_ONLY"):
            cfg.local_only = v.lower() in ("1", "true", "yes", "on")
        if v := env.get("OWNVOICEREADER_PLAYBACK"):
            cfg.playback = v
        if v := env.get("OWNVOICEREADER_LLM_PROVIDER"):
            cfg.llm_provider = v
        if v := env.get("OWNVOICEREADER_LLM_MODEL"):
            cfg.llm_model = v
        if v := env.get("OWNVOICEREADER_VOICE"):
            cfg.voice = v
        if v := env.get("OWNVOICEREADER_STYLE"):
            cfg.style = v
        return cfg

    def override_from_args(self, args: "argparse.Namespace") -> "Config":
        """Apply CLI flags with final precedence. Returns self for chaining."""
        import argparse  # noqa: F401 (kept for typing readers)

        a = vars(args)
        if a.get("pdf_path"):
            self.pdf_path = a["pdf_path"]
        if a.get("host"):
            self.host = a["host"]
        if a.get("port") is not None:
            self.port = a["port"]
        if a.get("no_browser"):
            self.open_browser = False
        if a.get("local_only"):
            self.local_only = True
        if a.get("no_cloud"):
            self.local_only = True
        if a.get("playback"):
            self.playback = a["playback"]
        if a.get("auto"):
            self.auto = True
        if a.get("director"):
            self.director = True
        if a.get("llm_model"):
            self.llm_model = a["llm_model"]
        if a.get("llm_provider"):
            self.llm_provider = a["llm_provider"]
        if a.get("voice"):
            self.voice = a["voice"]
        if a.get("style"):
            self.style = a["style"]
        if a.get("resume"):
            self.checkpoint_path = a["resume"]
        return self
