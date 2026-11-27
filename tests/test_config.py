"""M4: config layering + persistence.

    defaults  ->  ~/.ownvoicereader/config.yaml  ->  env (OWNVOICEREADER_*)  ->  CLI flags

These tests drive the layers with a *temp* config path (never the real
``~/.ownvoicereader/config.yaml``) so they stay hermetic. A missing or malformed
file must degrade to the lower layer, never crash.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ownvoicereader.config import (
    Config,
    apply_yaml,
    default_config_path,
    read_config_yaml,
    save_config,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _args(**kw) -> argparse.Namespace:
    """A stand-in CLI namespace (only the keys we set; the rest stay absent)."""
    a = argparse.Namespace()
    for k in ("pdf_path", "host", "port", "no_browser", "local_only", "no_cloud",
              "playback", "auto", "director", "llm_model", "llm_provider",
              "voice", "style"):
        if k not in kw:
            setattr(a, k, None)
        else:
            setattr(a, k, kw[k])
    return a


# --------------------------------------------------------------------------- #
# defaults
# --------------------------------------------------------------------------- #
def test_defaults():
    c = Config()
    assert c.host == "127.0.0.1"
    assert c.port == 6767
    assert c.local_only is True
    assert c.playback == "browser"
    assert c.llm_model is None
    assert c.voice is None
    assert c.style is None


# --------------------------------------------------------------------------- #
# the yaml layer
# --------------------------------------------------------------------------- #
def test_yaml_layer_applied(tmp_path):
    _write(tmp_path / "config.yaml", "llm_model: qwen3:8b\nvoice: en_US-lessac\nstyle: lecture\n")
    c = Config.from_env(tmp_path / "config.yaml", use_yaml=True, use_env=False)
    assert c.llm_model == "qwen3:8b"
    assert c.voice == "en_US-lessac"
    assert c.style == "lecture"


def test_missing_yaml_is_graceful(tmp_path):
    # A non-existent file degrades to defaults (no crash).
    c = Config.from_env(tmp_path / "nope.yaml", use_yaml=True, use_env=False)
    assert c.llm_model is None
    assert read_config_yaml(tmp_path / "nope.yaml") == {}


def test_malformed_yaml_is_graceful(tmp_path):
    p = _write(tmp_path / "config.yaml", "::: this is : not : valid : yaml : [")
    c = Config.from_env(p, use_yaml=True, use_env=False)
    assert c == Config()  # falls back entirely to defaults


def test_unknown_key_ignored(tmp_path):
    p = _write(tmp_path / "config.yaml", "llm_model: foo\nbogus_key: 123\n")
    c = Config.from_env(p, use_yaml=True, use_env=False)
    assert c.llm_model == "foo"
    assert not hasattr(c, "bogus_key")


def test_type_coercion(tmp_path):
    p = _write(tmp_path / "config.yaml",
               "port: '8080'\nlocal_only: 'false'\nopen_browser: 'yes'\n")
    c = Config.from_env(p, use_yaml=True, use_env=False)
    assert c.port == 8080            # string -> int
    assert c.local_only is False     # 'false' -> bool
    assert c.open_browser is True    # 'yes' -> bool


# --------------------------------------------------------------------------- #
# layer precedence: yaml < env < CLI
# --------------------------------------------------------------------------- #
def test_env_overrides_yaml(tmp_path, monkeypatch):
    _write(tmp_path / "config.yaml", "llm_model: from_yaml\n")
    monkeypatch.setenv("OWNVOICEREADER_LLM_MODEL", "from_env")
    c = Config.from_env(tmp_path / "config.yaml", use_yaml=True, use_env=True)
    assert c.llm_model == "from_env"


def test_cli_overrides_env(tmp_path, monkeypatch):
    _write(tmp_path / "config.yaml", "llm_model: from_yaml\n")
    monkeypatch.setenv("OWNVOICEREADER_LLM_MODEL", "from_env")
    c = Config.from_env(tmp_path / "config.yaml", use_yaml=True, use_env=True)
    c.override_from_args(_args(llm_model="from_cli"))
    assert c.llm_model == "from_cli"


def test_yaml_only_when_env_absent(tmp_path, monkeypatch):
    _write(tmp_path / "config.yaml", "style: narrative\n")
    monkeypatch.delenv("OWNVOICEREADER_STYLE", raising=False)
    c = Config.from_env(tmp_path / "config.yaml", use_yaml=True, use_env=True)
    assert c.style == "narrative"


def test_config_path_env_var(monkeypatch, tmp_path):
    # $OWNVOICEREADER_CONFIG points the default read at a custom location.
    p = _write(tmp_path / "custom.yaml", "voice: myvoice\n")
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(p))
    assert default_config_path() == p
    c = Config.from_env(use_yaml=True, use_env=False)  # no explicit path -> env
    assert c.voice == "myvoice"


def test_default_config_path_when_env_unset(monkeypatch, tmp_path):
    # No override -> the conventional ~/.ownvoicereader/config.yaml (expanded).
    monkeypatch.delenv("OWNVOICEREADER_CONFIG", raising=False)
    p = default_config_path()
    assert p == Path("~/.ownvoicereader/config.yaml").expanduser()
    assert p.name == "config.yaml"
    assert p.parent.name == ".ownvoicereader"


def test_port_invalid_falls_back(tmp_path):
    # A non-numeric port in the file is uncoercible -> skipped, so the default
    # (6767) survives rather than crashing the reader on a bad config.
    _write(tmp_path / "config.yaml", "port: not-a-number\n")
    c = Config.from_env(tmp_path / "config.yaml", use_yaml=True, use_env=False)
    assert c.port == 6767


def test_bool_coercion_from_non_string(tmp_path):
    # YAML may hand a boolean field a real bool (``false``) or an int (``1``);
    # both coerce, and a real bool is returned as-is.
    p = _write(tmp_path / "config.yaml", "local_only: 1\nopen_browser: false\n")
    c = Config.from_env(p, use_yaml=True, use_env=False)
    assert c.local_only is True        # int 1 -> True
    assert c.open_browser is False      # a real YAML bool


def test_ollama_host_roundtrip(tmp_path):
    # M4: the Ollama host is a durable setting the "Save" button persists.
    c = Config(ollama_host="http://10.0.0.5:11434")
    p = save_config(c, tmp_path / "config.yaml")
    assert "ollama_host" in p.read_text()
    assert Config.load(p).ollama_host == "http://10.0.0.5:11434"


def test_ollama_host_env_layer(tmp_path, monkeypatch):
    # The host can also come from an OWNVOICEREADER_* env var — a layer above the
    # file (so an exported var overrides a saved one, the same precedence as model).
    monkeypatch.setenv("OWNVOICEREADER_OLLAMA_HOST", "http://env-host:11434")
    c = Config.from_env(use_yaml=False, use_env=True)
    assert c.ollama_host == "http://env-host:11434"


def test_no_credentials_are_ever_persisted(tmp_path, monkeypatch):
    """A6 (local-only) — the app pings **no proprietary cloud provider**.

    Since the cloud API tokens were removed, the old "no credential is ever
    persisted" rule is re-expressed for a local-only app: the *whole configuration
    surface* (the read-only env allow-list + the settings the "Save" button writes)
    must reference **no proprietary cloud provider** and hold **no credential-shaped
    field** — the only provider is the local Ollama server (an open endpoint the user
    runs) plus on-machine TTS. A save with every provider env var exported therefore
    leaks nothing secret. If a cloud provider is ever wired back into the config,
    this fails."""
    from ownvoicereader.config import CLOUD_PROVIDERS, PERSIST_FIELDS, PROVIDER_ENV_VARS

    # (a) The blacklist is non-trivial: there ARE providers we refuse to ping.
    assert CLOUD_PROVIDERS, "expected a non-empty proprietary-cloud blacklist"

    # (b) No proprietary cloud provider, and no credential-shaped name, anywhere in
    #     the config surface — the env allow-list or the persisted settings.
    surface = list(PROVIDER_ENV_VARS) + list(PERSIST_FIELDS)
    for name in surface:
        assert not any(cloud in name.lower() for cloud in CLOUD_PROVIDERS), \
            "proprietary cloud provider in the config surface: %r" % name
        assert not any(tok in name for tok in ("_KEY", "TOKEN", "SECRET", "_API")), \
            "credential-shaped field in the config surface: %r" % name

    # (c) Out of the box the provider is the local Ollama server and cloud egress is
    #     forbidden — nothing proprietary is pinged by default.
    default = Config()
    assert default.local_only is True
    assert (default.llm_provider or "ollama").lower() not in CLOUD_PROVIDERS

    # (d) Even with every provider env var exported, a save writes no provider env
    #     var name and leaks no value — the file holds only local settings,
    #     including the Ollama host *endpoint* (which is not a credential).
    for k in PROVIDER_ENV_VARS:
        monkeypatch.setenv(k, "SUPER-SECRET-VALUE")
    c = Config(
        llm_model="qwen3:8b", voice="en_US-lessac",
        ollama_host="http://localhost:11434", local_only=True,
        open_browser=False, host="127.0.0.1", port=6767,
        playback="browser", director=False, llm_provider="ollama",
        style="lecture",
    )
    written = save_config(c, tmp_path / "config.yaml").read_text()
    # No provider env var name or value leaks into the file:
    assert "SUPER-SECRET-VALUE" not in written
    for k in PROVIDER_ENV_VARS:
        assert k not in written
    # but every durable, non-secret local setting IS written — including the endpoint:
    for f in PERSIST_FIELDS:
        assert f in written
    assert Config.load(tmp_path / "config.yaml").ollama_host == "http://localhost:11434"


# --------------------------------------------------------------------------- #
# persistence: save / load round-trip
# --------------------------------------------------------------------------- #
def test_to_yaml_dict_skips_none_and_transient():
    c = Config(llm_model="m", voice="v", pdf_path="p.pdf")
    d = c.to_yaml_dict()
    assert d["llm_model"] == "m"
    assert d["voice"] == "v"
    # transient per-run fields are never persisted
    assert "pdf_path" not in d
    # the durable booleans are always explicit
    assert d["local_only"] is True


def test_save_then_load_roundtrip(tmp_path):
    c = Config(llm_model="qwen3:8b", voice="en_US-lessac", style="lecture",
               local_only=False, port=8080)
    written = save_config(c, tmp_path / "config.yaml")
    assert written.exists()
    loaded = Config.load(tmp_path / "config.yaml")
    assert loaded.llm_model == "qwen3:8b"
    assert loaded.voice == "en_US-lessac"
    assert loaded.style == "lecture"
    assert loaded.local_only is False
    assert loaded.port == 8080


def test_save_creates_parent_dir(tmp_path):
    target = tmp_path / "nested" / "config.yaml"
    save_config(Config(llm_model="m"), target)
    assert target.exists()


def test_save_is_atomic(tmp_path):
    # The temp-swap means a partial write never truncates the real file.
    p = tmp_path / "config.yaml"
    save_config(Config(llm_model="first"), p)
    save_config(Config(llm_model="second"), p)
    loaded = Config.load(p)
    assert loaded.llm_model == "second"


def test_apply_yaml_ignores_empty(tmp_path):
    c = Config()
    apply_yaml(c, None)
    apply_yaml(c, {})
    assert c == Config()
