"""LLM layer — local Ollama client (via the `ollama` library, M2.5) + explainer.

M2.5 wires a vision-capable local LLM (default ``lfm3-vl:2b``) that the explainer
uses to turn a figure/table image into a short, spoken description. The client
talks to a *local* Ollama server through the ``ollama`` library — no litellm, no
httpx — and degrades gracefully to the A2 caption+context base when it is absent.
"""

from .client import (
    DEFAULT_MODEL,
    LlmClient,
    LlmUnavailable,
    NullLlmClient,
    OllamaClient,
    build_llm_client,
    is_vision_available,
    resolve_model,
)
from .explainer import Explainer, link_media

__all__ = [
    "DEFAULT_MODEL",
    "LlmClient",
    "LlmUnavailable",
    "NullLlmClient",
    "OllamaClient",
    "build_llm_client",
    "is_vision_available",
    "resolve_model",
    "Explainer",
    "link_media",
]
