"""Model clients. Every provider returns JSON constrained to a supplied schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from algoverse.ai.schemas import AiConfig

TEMPERATURE = 0.1
KEEP_ALIVE = "10m"


class LLMError(Exception):
    """The model could not be reached or did not answer."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str, schema: dict) -> LLMResult: ...


class OllamaClient:
    def __init__(self, config: AiConfig, client: httpx.Client | None = None):
        self.model = config.model
        self._think = config.think
        self._context_tokens = config.context_tokens
        self._client = client or httpx.Client(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )

    def complete(self, system: str, user: str, schema: dict) -> LLMResult:
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "keep_alive": KEEP_ALIVE,
            "options": {"temperature": TEMPERATURE, "num_ctx": self._context_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._think is not None:
            payload["think"] = self._think
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise LLMError(f"Ollama returned {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError("Ollama returned a body that is not JSON") from exc
        return LLMResult(
            text=body.get("message", {}).get("content", ""),
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
        )


def build_client(config: AiConfig) -> LLMClient:
    return OllamaClient(config)
