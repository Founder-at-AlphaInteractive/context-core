"""Groq provider using the OpenAI-compatible chat completions endpoint."""

from __future__ import annotations

import httpx

from app.config import settings
from app.providers.base import AIProvider, AIProviderError


class GroqProvider(AIProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip())

    def complete_json(self, *, system: str, user: str) -> str:
        if not self.is_configured():
            raise AIProviderError("Groq API key is not configured")

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            # Sanitize error to avoid leaking request headers or query params
            raise AIProviderError(f"Groq request failed: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            # Do NOT leak the API key, full body, or sensitive data.
            # Truncate response text safely.
            snippet = response.text[:200] if response.text else ""
            # Strip any potential token occurrences if somehow reflected
            if self._api_key in snippet:
                snippet = snippet.replace(self._api_key, "***REDACTED***")
            raise AIProviderError(
                f"Groq returned HTTP {response.status_code}: {snippet}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AIProviderError("Groq response was not valid JSON") from exc

        try:
            choices = data["choices"]
            if not choices or not isinstance(choices, list):
                raise AIProviderError("Groq response missing choices array")
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(
                "Groq response did not contain the expected chat completion shape"
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("Groq returned empty content")

        return content


def build_default_provider() -> AIProvider | None:
    """Build the provider configured in environment settings.

    Returns None if no provider is configured (e.g. GROQ_API_KEY unset).
    """
    if not settings.groq_api_key or not settings.groq_api_key.strip():
        return None
    return GroqProvider(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        base_url=settings.groq_base_url,
    )
