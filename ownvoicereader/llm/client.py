"""llm/client.py — M2.5: the LLM client (local Ollama via the `ollama` library).

The reader explains figures and tables with a *vision-capable* local LLM — the
default is ``lfm3-vl:2b`` (a small Llama that both reads an image and answers).
It talks to a local Ollama server through the **`ollama`** Python library — *not*
httpx, *not* litellm (A6, M2.5). This module is the single interface the
explainer (:mod:`ownvoicereader.llm.explainer`) uses to turn a figure/table
image into a short, *spoken* description.

Design mirrors :mod:`ownvoicereader.voice.tts_engine`:

  * :class:`LlmClient`   — the abstract interface the explainer depends on.
  * :class:`OllamaClient` — the real client, driving the ``ollama`` library.
  * :class:`NullLlmClient` — a no-op fallback when the library or server is
    unavailable, so the reader degrades to the A2 *caption+context* base instead
    of crashing (graceful, the M6 "LLM-down" path).
  * :func:`build_llm_client` — the factory: returns the Ollama client when the
    ``[vision]`` extra is installed *and* a server answers for the configured
    model; otherwise ``None``.

Everything here is optional at import time: ``ollama`` lives in the ``[vision]``
extra, so a bare core install must import this module **without** the library
present. The library is therefore imported *lazily* inside the client, never at
module load.

Configurability (A6): the model comes from ``config.llm_model`` (a.k.a.
``OWNVOICEREADER_LLM_MODEL`` / ``--llm``) and falls back to
:data:`DEFAULT_MODEL`; the server host comes from the ``OLLAMA_HOST`` environment
variable so a *different* Ollama server can be pointed at, with no value ever
persisted by us (secrets stay in the OS environment, A6).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Optional, Sequence, Union

from ..config import Config

log = logging.getLogger("ownvoicereader.llm")

# The default vision model for M2.5: a small, local, vision-capable Llama that
# reads a figure/table image and answers. The user overrides it with --llm /
# OWNVOICEREADER_LLM_MODEL to match whatever is pulled on their machine.
DEFAULT_MODEL = "lfm3-vl:2b"
# Ollama's default local server (matches the `ollama` library's own default).
DEFAULT_HOST = "http://localhost:11434"
# How long Ollama keeps the model resident after a call, so a background batch of
# figure/table descriptions does not reload the model between each one.
_DEFAULT_KEEP_ALIVE = "5m"

# A message is a chat turn. A *vision* message additionally carries the image
# bytes (a list, per the `ollama` library's contract).
Message = dict[str, Any]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class LlmUnavailable(Exception):
    """The client could not produce a response (library missing, server down, or
    the model is not present). Callers treat this as "no vision", falling back to
    the A2 caption+context base rather than failing the read."""


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class LlmClient(ABC):
    """The minimal chat interface the explainer depends on.

    The explainer never imports a provider directly; it asks a :class:`LlmClient`
    for a response. That keeps the *local-only* decision clean and lets M2.5 layer
    a vision model on top without changing the explainer.
    """

    name: str = "abstract"
    model: str = DEFAULT_MODEL

    @property
    @abstractmethod
    def available(self) -> bool:
        """True if the client can reach a *model* right now. Best-effort + cached;
        a :meth:`refresh` re-checks. A false result degrades the reader, it does
        not crash it."""

    @abstractmethod
    def _chat_sync(
        self,
        model: Optional[str],
        messages: Sequence[Message],
        stream: bool,
        options: Optional[dict],
    ) -> Union[str, Iterable[str]]:
        """Blocking chat. Returns the full text when ``stream`` is False, else an
        iterator of token strings. Runs the actual provider call (in the Ollama
        client, the ``ollama`` library)."""

    def refresh(self) -> "LlmClient":
        """Force the next :attr:`available` check to re-probe the server."""
        return self

    def chat(
        self,
        model: Optional[str] = None,
        messages: Optional[Sequence[Message]] = None,
        *,
        stream: bool = False,
        options: Optional[dict] = None,
    ) -> Union[str, Iterable[str]]:
        """Send a chat turn and get the response.

        ``messages`` is a list of ``{"role", "content", "images"?}`` turns; a
        vision turn sets ``"images": [<png bytes>]``. When ``stream`` is True the
        return value is an iterator of token strings (the provider streams)."""
        model = model or self.model
        messages = messages or []
        return self._chat_sync(model, messages, stream, options)

    def describe(
        self,
        image: bytes,
        prompt: str,
        *,
        model: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> str:
        """Convenience: a one-image, one-prompt vision turn. Returns the model's
        text answer (empty string on no image / empty answer)."""
        if not image:
            return ""
        out = self.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": [image]}],
            options=options,
        )
        return out if isinstance(out, str) else "".join(out)

    def list_models(self) -> list[str]:
        """The model ids the server reports (``[]`` when unreachable)."""
        return []

    def has_model(self, model: Optional[str] = None) -> bool:
        """True if ``model`` (or the client's default) is present on the server."""
        return False

    async def achat(
        self,
        model: Optional[str] = None,
        messages: Optional[Sequence[Message]] = None,
        *,
        stream: bool = False,
        options: Optional[dict] = None,
    ) -> Union[str, Iterable[str]]:
        """Async wrapper around :meth:`chat` (runs the blocking call in a thread,
        mirroring :meth:`TtsEngine.synthesize`)."""
        loop = __import__("asyncio").get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.chat(model, messages, stream=stream, options=options)
        )


# --------------------------------------------------------------------------- #
# Ollama (the real client)
# --------------------------------------------------------------------------- #
class OllamaClient(LlmClient):
    """Drive a local Ollama server through the ``ollama`` library.

    The ``ollama`` library is in the ``[vision]`` extra, so it is imported
    *lazily* — constructing an :class:`OllamaClient` without the library raises
    :class:`ImportError` (caught by :func:`build_llm_client`), which is exactly
    the signal that vision is not installed.
    """

    name = "ollama"

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        keep_alive: str = _DEFAULT_KEEP_ALIVE,
    ):
        try:
            import ollama  # noqa: F401 (imported for the side-effect of validating)
        except Exception as exc:  # pragma: no cover - only when [vision] absent
            raise ImportError(
                "the `ollama` library is not installed; "
                "install the vision extra:  pip install 'ownvoicereader[vision]'"
            ) from exc
        self._ollama = ollama
        self.model = model or DEFAULT_MODEL
        self._host = host
        self._keep_alive = keep_alive
        # A fresh client per construction is fine (it is a thin handle); we reuse
        # one and only rebuild if the host changes.
        self._client = None
        # Cache the reachability/model probe so repeated `available` checks are
        # cheap; `refresh()` invalidates it.
        self._available: Optional[bool] = None

    # -- server handle ----------------------------------------------------- #
    @property
    def _handle(self):
        """The underlying ``ollama.Client`` (built once, bound to the host)."""
        if self._client is None:
            # When no host is given, the `ollama` library itself falls back to the
            # OLLAMA_HOST env var (if set) then its default — so a user who exports
            # OLLAMA_HOST is honoured without us persisting anything.
            host = self._host
            if host is None and os.environ.get("OLLAMA_HOST"):
                host = os.environ["OLLAMA_HOST"]
            self._client = self._ollama.Client(host=host) if host else self._ollama.Client()
        return self._client

    # -- probes ------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            self._probe()
            self._available = True
        except LlmUnavailable:
            self._available = False
        except Exception:  # pragma: no cover - defensive
            log.warning("LLM availability probe failed", exc_info=True)
            self._available = False
        return self._available

    def refresh(self) -> "OllamaClient":
        """Force the next :attr:`available` to re-probe (server may have come up)."""
        self._available = None
        return self

    def _probe(self) -> None:
        """Raise :class:`LlmUnavailable` unless the server answers *and* the model
        is present. A missing model is not fatal to *reaching* the server, but the
        reader's vision path needs *this* model, so we treat it as unavailable."""
        try:
            models = self.list_models()
        except Exception as exc:
            raise LlmUnavailable(
                f"Ollama server unreachable at {self._host or DEFAULT_HOST}: {exc}"
            ) from exc
        if not models:
            raise LlmUnavailable(f"no models on Ollama at {self._host or DEFAULT_HOST}")
        if not self.has_model(self.model):
            # Do not fail hard: a reachable server with a *different* model still
            # means the reader can speak the A2 base; vision just won't enhance.
            log.info("configured vision model %r not on server (have: %s)",
                     self.model, ", ".join(models))
            return
        log.info("LLM: using Ollama model %s at %s",
                 self.model, self._host or DEFAULT_HOST)

    def list_models(self) -> list[str]:
        out: list[str] = []
        try:
            resp = self._handle.list()
        except Exception as exc:  # pragma: no cover - server-dependent
            raise LlmUnavailable(f"could not list models: {exc}") from exc
        for m in getattr(resp, "models", []) or []:
            # The `ollama` Model object stores its id in the `model` field
            # ("lfm3-vl:2b"); older/loose dicts use "name".
            name = getattr(m, "model", None) or (m.get("name") if isinstance(m, dict) else None)
            if name:
                out.append(name)
        return out

    def has_model(self, model: Optional[str] = None) -> bool:
        model = model or self.model
        return any(m == model or m.split(":")[0] == model.split(":")[0]
                   for m in self.list_models())

    # -- chat -------------------------------------------------------------- #
    def _chat_sync(
        self,
        model: Optional[str],
        messages: Sequence[Message],
        stream: bool,
        options: Optional[dict],
    ) -> Union[str, Iterable[str]]:
        model = model or self.model
        if options is None:
            options = {"temperature": 0.0}
        try:
            res = self._handle.chat(
                model=model,
                messages=list(messages),
                stream=stream,
                keep_alive=self._keep_alive,
                options=options,
            )
        except Exception as exc:  # pragma: no cover - server-dependent
            raise LlmUnavailable(f"chat failed on model {model!r}: {exc}") from exc

        if not stream:
            # A single ChatResponse; the answer is message.content.
            content = getattr(res, "message", None)
            content = content.content if content is not None else ""
            return content or ""
        return self._iter_stream(res)

    @staticmethod
    def _iter_stream(res: Iterable[Any]) -> Iterable[str]:
        """Yield the incremental token strings from a streamed response."""
        for chunk in res:
            tok = getattr(chunk, "message", None)
            tok = tok.content if tok is not None else ""
            if tok:
                yield tok
            if getattr(chunk, "done", False):
                break


# --------------------------------------------------------------------------- #
# Null client (the graceful fallback)
# --------------------------------------------------------------------------- #
class NullLlmClient(LlmClient):
    """A client that reports *unavailable* and raises on use — the reader's
    "vision is off" state. It lets the explainer degrade to the A2 caption+context
    base with a single code path instead of branching on ``None`` everywhere."""

    name = "null"

    @property
    def available(self) -> bool:
        return False

    def _chat_sync(self, model, messages, stream, options):
        raise LlmUnavailable("no LLM client (vision extra not installed or server down)")

    def has_model(self, model: Optional[str] = None) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def resolve_model(config: Optional[Config] = None, model: Optional[str] = None) -> str:
    """The vision model id, resolved: explicit > config > default.

    (``Config.from_env`` already folds ``OWNVOICEREADER_LLM_MODEL`` into
    ``config.llm_model``, so the env layer is honoured here via the config.)
    """
    if model:
        return model
    if config and config.llm_model:
        return config.llm_model
    return DEFAULT_MODEL


def build_llm_client(
    config: Optional[Config] = None,
    *,
    model: Optional[str] = None,
    host: Optional[str] = None,
    null: bool = True,
) -> LlmClient:
    """Build the LLM client the explainer will use.

    Returns an :class:`OllamaClient` when the ``[vision]`` extra is installed;
    otherwise (no ``ollama`` library) it returns a :class:`NullLlmClient` when
    ``null`` (the default) so the explainer always has a client to ask — and a
    single code path — and simply degrades to the A2 base. ``available`` is
    *not* probed here (a network round-trip); the caller checks it lazily.
    """
    chosen = resolve_model(config, model)
    try:
        return OllamaClient(model=chosen, host=host)
    except ImportError:
        log.info("LLM: `ollama` library absent — vision is off; using the A2 base")
        if not null:
            raise
        return NullLlmClient()


def is_vision_available(config: Optional[Config] = None,
                        model: Optional[str] = None,
                        host: Optional[str] = None) -> bool:
    """True if a vision model can be reached right now (library present *and* a
    server answers *and* the model is present). A convenience for the UI / banner
    and the explainer's "enhance when ready" decision."""
    client = build_llm_client(config, model=model, host=host)
    try:
        return client.available
    except Exception:  # pragma: no cover - defensive
        return False
