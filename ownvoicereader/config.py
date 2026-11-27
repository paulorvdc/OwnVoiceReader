"""Configuration for OwnVoiceReader.

Resolution layers (highest precedence last):

    defaults  ->  ~/.ownvoicereader/config.yaml  ->  env (OWNVOICEREADER_*)  ->  CLI flags

The config file is the user's *saved* settings — the model / voice / style they
pick in the viewer, written by the "Save" button (see
:func:`ownvoicereader.server.api.save_settings`). It sits *below* the
environment, so an exported ``OWNVOICEREADER_*`` var still wins, and below the
CLI, so a flag is the final word. The file is **optional and best-effort**: a
missing or malformed file is ignored, so the reader never crashes on it.

API **credentials** (``OPENAI_API_KEY``, ``ELEVENLABS_API_KEY``, …) are read
**from the OS environment only** and are never written to disk (A6) — this keeps
secrets out of our hands. The config file holds only *settings*, never secrets. A
host *endpoint* such as ``ollama_host`` (the local Ollama server, e.g.
``http://localhost:11434``) is not a credential, so it is a saved setting the user
can change in the viewer; the ollama library's own ``OLLAMA_HOST`` env var is still
read live for the fallback when no host is saved.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ownvoicereader.config")

# The config file's location, overridable so tests / custom setups can point
# elsewhere. Read live (so a shell can export it in the same session).
CONFIG_PATH_ENV = "OWNVOICEREADER_CONFIG"
DEFAULT_CONFIG_PATH = Path("~/.ownvoicereader/config.yaml")

# A small allow-list of environment variables we *read* for provider access.
# These are passed through to the LLM/TTS clients untouched; we never persist
# them. Documented in the README so the user knows exactly what is read.
PROVIDER_ENV_VARS: tuple[str, ...] = (
    "OLLAMA_HOST",
    "OLLAMA_MODELS",
)


def provider_env() -> dict[str, str]:
    """Return the subset of the OS environment relevant to providers.

    Read live at call time so a user can export a key in the same shell.
    """
    return {k: os.environ[k] for k in PROVIDER_ENV_VARS if k in os.environ}


# --------------------------------------------------------------------------- #
# Config-file location + YAML helpers
# --------------------------------------------------------------------------- #
def default_config_path() -> Path:
    """The config file: ``$OWNVOICEREADER_CONFIG`` if set, else the default."""
    env = os.environ.get(CONFIG_PATH_ENV)
    return Path(env).expanduser() if env else DEFAULT_CONFIG_PATH.expanduser()


# Settings a user persists to config.yaml. ``pdf_path`` is per-run (transient)
# and is deliberately NOT saved — it is an argument, not a setting. Everything
# here is a durable user choice.
PERSIST_FIELDS: tuple[str, ...] = (
    "host", "port", "open_browser", "local_only",
    "playback", "director", "llm_provider", "llm_model", "ollama_host", "voice", "style",
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_INT_FIELDS = {"port"}
_BOOL_FIELDS = {"local_only", "open_browser", "director"}


def _coerce(name: str, value: Any):
    """Coerce a raw value (from YAML or the environment) to a field's type.

    Returns ``None`` when the value is unusable, so the caller skips it and the
    lower layer's value survives.
    """
    if value is None:
        return None
    if name in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if name in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _TRUE:
                return True
            if v in _FALSE:
                return False
            return None
        return bool(value)
    return str(value)


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
    # M4: the local Ollama server endpoint (e.g. "http://localhost:11434"). When
    # set it points the vision client at a *different* server; unset means "use the
    # OLLAMA_HOST env var, else the library default." A host is an *endpoint*, not a
    # credential, so it is a durable setting the user saves (see PERSIST_FIELDS).
    ollama_host: Optional[str] = None
    voice: Optional[str] = None
    style: Optional[str] = None        # academic-calm | quick-scan | narrative | lecture

    # --- resolution (M4 layering) -------------------------------------- #
    @classmethod
    def from_env(cls, config_path: Optional[str | Path] = None, *,
                 use_yaml: bool = True, use_env: bool = True) -> "Config":
        """Build a Config, layering defaults -> config.yaml -> OWNVOICEREADER_*.

        ``config_path`` overrides the file to read (else ``$OWNVOICEREADER_CONFIG``
        or :func:`default_config_path`). A missing/bad file is ignored, so the
        caller always gets a usable Config. Both layers can be disabled for tests.
        """
        cfg = cls()
        if use_yaml:
            apply_yaml(cfg, read_config_yaml(config_path))
        if use_env:
            _apply_env(cfg)
        return cfg

    @classmethod
    def from_yaml(cls, path: Optional[str | Path] = None) -> "Config":
        """defaults -> config.yaml only (no env layer — the *saved* settings as a
        Config, used by tests and by the "what did I save?" read-back)."""
        return cls.from_env(path, use_yaml=True, use_env=False)

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
        return self

    # --- persistence (M4) ---------------------------------------------- #
    def to_yaml_dict(self) -> dict:
        """The *persistent* settings as a plain mapping, skipping unset (None)
        ones so the saved file stays minimal. Transient per-run fields (pdf_path)
        are never included."""
        out: dict = {}
        for name in PERSIST_FIELDS:
            val = getattr(self, name, None)
            if val is not None:
                out[name] = val
        return out

    def save(self, path: Optional[str | Path] = None) -> Path:
        """Write the persistent settings to ``path`` (else the default)."""
        return save_config(self, path)

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Config":
        """defaults -> config.yaml (no env) — the saved settings as a Config."""
        return cls.from_yaml(path)


# --------------------------------------------------------------------------- #
# Layer application + read/write
# --------------------------------------------------------------------------- #
def _apply_env(cfg: "Config") -> None:
    """Apply the OWNVOICEREADER_* environment layer over a Config."""
    env = os.environ
    if v := env.get("OWNVOICEREADER_HOST"):
        cfg.host = v
    if v := env.get("OWNVOICEREADER_PORT"):
        try:
            cfg.port = int(v)
        except ValueError:
            pass
    if v := env.get("OWNVOICEREADER_LOCAL_ONLY"):
        cfg.local_only = v.strip().lower() in _TRUE
    if v := env.get("OWNVOICEREADER_PLAYBACK"):
        cfg.playback = v
    if v := env.get("OWNVOICEREADER_LLM_PROVIDER"):
        cfg.llm_provider = v
    if v := env.get("OWNVOICEREADER_LLM_MODEL"):
        cfg.llm_model = v
    if v := env.get("OWNVOICEREADER_OLLAMA_HOST"):
        cfg.ollama_host = v
    if v := env.get("OWNVOICEREADER_VOICE"):
        cfg.voice = v
    if v := env.get("OWNVOICEREADER_STYLE"):
        cfg.style = v


def apply_yaml(cfg: "Config", data: Any) -> None:
    """Apply a config-file mapping (from YAML) over a Config, coercing each known
    field and ignoring unknown / uncoercible ones (logged, never fatal)."""
    if not data:
        return
    names = {f.name for f in fields(Config)}
    for key, raw in data.items():
        if key not in names:
            log.info("ignoring unknown config key: %r", key)
            continue
        val = _coerce(key, raw)
        if val is None:
            continue
        setattr(cfg, key, val)


def read_config_yaml(path: Optional[str | Path] = None) -> dict:
    """Read and parse the config file, returning a mapping (``{}`` if absent /
    unreadable / not a mapping). Never raises — a broken config must not stop the
    reader from starting."""
    p = Path(path).expanduser() if path else default_config_path()
    if not p.is_file():
        return {}
    try:
        import yaml
    except Exception:  # pragma: no cover - PyYAML is a core dep
        log.warning("PyYAML not installed; ignoring %s", p)
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("could not read %s (%s); ignoring", p, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("%s is not a mapping; ignoring", p)
        return {}
    return data


def save_config(cfg: "Config", path: Optional[str | Path] = None) -> Path:
    """Write a Config's persistent settings to ``path`` (else the default),
    creating parent dirs. The write is atomic (temp file + rename) so a crash mid-
    write can't leave a truncated config. Returns the path written."""
    import yaml

    p = Path(path).expanduser() if path else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_yaml_dict()
    header = (
        "# OwnVoiceReader settings — written by the viewer's 'Save' button.\n"
        "# Resolution layers (highest precedence last):\n"
        "#   defaults -> this file -> OWNVOICEREADER_* env -> CLI flags\n"
    )
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(
        header + yaml.safe_dump(data, sort_keys=True, default_flow_style=False),
        encoding="utf-8",
    )
    tmp.replace(p)
    log.info("saved settings to %s: %s", p, ", ".join(sorted(data)) or "(none)")
    return p
