"""Streaming client for any OpenAI-compatible chat API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import AsyncOpenAI

from monika.config import Settings


class LLMError(Exception):
    """LLM failure whose message is already in French and ready to display."""


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer text, as soon as it arrives."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """A complete function call requested by the model."""

    id: str
    name: str
    arguments: str  # Raw JSON string, parsed later by the agent loop


@dataclass
class Completion:
    """The full assistant turn, yielded last by the stream."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

    def as_message(self) -> dict[str, Any]:
        """Return the turn as an OpenAI assistant message for the history."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in self.tool_calls
            ]
        elif not self.content:
            message["content"] = ""
        return message


class LLMClient:
    """Async wrapper that streams chat completions and accumulates tool calls."""

    def __init__(self, settings: Settings, http_client: Any | None = None) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            # Local servers often need no key, but the SDK refuses an empty one
            api_key=settings.openai_api_key.get_secret_value() or "sans-cle",
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            http_client=http_client,
        )
        self.last_call: dict[str, Any] | None = (
            None  # "Last request worked / failed", for the health grid
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[TextDelta | Completion]:
        """Yield TextDelta pieces, then one final Completion."""
        kwargs: dict[str, Any] = {
            "model": model or self._settings.model_name,
            "messages": messages,
            "temperature": self._settings.llm_temperature
            if temperature is None
            else temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        try:
            async with await self._client.chat.completions.create(**kwargs) as response:
                async for chunk in response:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    # reasoning_content (thinking models) is deliberately ignored
                    if delta.content:
                        parts.append(delta.content)
                        yield TextDelta(delta.content)
                    for tc in delta.tool_calls or []:
                        self._merge_tool_call(calls, tc)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.OpenAIError as exc:
            error = self._translate(exc)
            self.last_call = {"ok": False, "detail": str(error)}
            raise error from exc
        self.last_call = {"ok": True, "detail": f"{len(parts)} caractère(s) reçus"}

        tool_calls = [
            ToolCall(
                id=c["id"] or f"call_{i}",
                name=c["name"],
                arguments=c["arguments"] or "{}",
            )
            for i, c in sorted(calls.items())
        ]
        yield Completion("".join(parts), tool_calls, finish)

    async def ask(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> Completion:
        """Run a request to the end and return the final Completion."""
        final: Completion | None = None
        async for item in self.stream(messages, tools, temperature, model):
            if isinstance(item, Completion):
                final = item
        assert final is not None
        return final

    async def ping(self, timeout_s: float = 1.5) -> str:
        """Cheap connectivity probe for the health grid: ask the provider for its model list.

        It never sends a completion (that would cost money and change nothing a turn needs).
        """
        models = await asyncio.wait_for(self._client.models.list(), timeout_s)
        return f"{len(models.data)} modèle(s) annoncé(s)"

    @staticmethod
    def _merge_tool_call(calls: dict[int, dict[str, str]], tc: Any) -> None:
        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
        if tc.id:
            slot["id"] = tc.id
        if tc.function:
            name = tc.function.name
            # Some providers resend the full name in every chunk: only append real fragments
            if name and (not slot["name"] or name != slot["name"]):
                slot["name"] += name
            if tc.function.arguments:
                slot["arguments"] += tc.function.arguments

    def _translate(self, exc: openai.OpenAIError) -> LLMError:
        url, model = self._settings.llm_base_url, self._settings.model_name
        if isinstance(exc, openai.APITimeoutError):
            return LLMError(
                f"Le LLM n'a pas répondu à temps (LLM_TIMEOUT_S={self._settings.llm_timeout_s:g})."
            )
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(
                f"Impossible de joindre le LLM à {url} (réseau, URL ou serveur arrêté)."
            )
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMError(f"Clé API refusée par {url} : vérifie OPENAI_API_KEY.")
        if isinstance(exc, openai.NotFoundError):
            return LLMError(
                f"Modèle ou adresse introuvable (MODEL_NAME={model}, LLM_BASE_URL={url})."
            )
        if isinstance(exc, openai.RateLimitError):
            return LLMError(
                "Limite de requêtes ou de crédit atteinte côté fournisseur. Réessaie plus tard."
            )
        if isinstance(exc, openai.BadRequestError):
            return LLMError(f"Requête refusée par le LLM : {exc.message}")
        if isinstance(exc, openai.APIStatusError):
            return LLMError(f"Erreur {exc.status_code} du LLM : {exc.message}")
        return LLMError(f"Erreur du client LLM : {exc}")
