"""Tests for GroqProvider and AIProvider abstraction."""

import httpx
import pytest

from app.providers.base import AIProviderError
from app.providers.groq_provider import GroqProvider, build_default_provider


def test_groq_provider_configuration():
    provider = GroqProvider(
        api_key="gsk-test-key",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
    )
    assert provider.is_configured() is True


def test_groq_provider_missing_key():
    provider = GroqProvider(
        api_key="",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
    )
    assert provider.is_configured() is False
    with pytest.raises(AIProviderError, match="Groq API key is not configured"):
        provider.complete_json(system="sys", user="usr")


def test_groq_provider_successful_response(monkeypatch):
    provider = GroqProvider(
        api_key="gsk-valid-key",
        model="llama-test",
        base_url="https://api.groq.com/openai/v1",
    )

    def mock_post(client, url, headers, json):
        assert headers["Authorization"] == "Bearer gsk-valid-key"
        assert json["model"] == "llama-test"
        assert json["response_format"] == {"type": "json_object"}
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {"message": {"content": '{"items": [{"title": "Test"}]}'}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    res = provider.complete_json(system="sys", user="usr")
    assert res == '{"items": [{"title": "Test"}]}'


def test_groq_provider_http_error(monkeypatch):
    provider = GroqProvider(
        api_key="gsk-secret-key-12345",
        model="llama-test",
        base_url="https://api.groq.com/openai/v1",
    )

    def mock_post(client, url, headers, json):
        return httpx.Response(
            status_code=401,
            text='{"error": "Invalid API Key gsk-secret-key-12345"}',
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    with pytest.raises(AIProviderError) as exc_info:
        provider.complete_json(system="sys", user="usr")

    err_msg = str(exc_info.value)
    assert "401" in err_msg
    # Ensure raw API key is redacted/not leaked
    assert "gsk-secret-key-12345" not in err_msg
    assert "***REDACTED***" in err_msg


def test_groq_provider_network_failure(monkeypatch):
    provider = GroqProvider(
        api_key="gsk-test",
        model="llama-test",
        base_url="https://api.groq.com/openai/v1",
    )

    def mock_post(client, url, headers, json):
        raise httpx.ConnectError("Network is down")

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    with pytest.raises(AIProviderError, match="Groq request failed: ConnectError"):
        provider.complete_json(system="sys", user="usr")


def test_groq_provider_malformed_json_envelope(monkeypatch):
    provider = GroqProvider(
        api_key="gsk-test",
        model="llama-test",
        base_url="https://api.groq.com/openai/v1",
    )

    def mock_post(client, url, headers, json):
        return httpx.Response(
            status_code=200,
            text="Non-JSON response from server",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    with pytest.raises(AIProviderError, match="Groq response was not valid JSON"):
        provider.complete_json(system="sys", user="usr")


def test_groq_provider_missing_choices(monkeypatch):
    provider = GroqProvider(
        api_key="gsk-test",
        model="llama-test",
        base_url="https://api.groq.com/openai/v1",
    )

    def mock_post(client, url, headers, json):
        return httpx.Response(
            status_code=200,
            json={"choices": []},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    with pytest.raises(AIProviderError, match="missing choices array"):
        provider.complete_json(system="sys", user="usr")


def test_groq_provider_empty_content(monkeypatch):
    provider = GroqProvider(
        api_key="gsk-test",
        model="llama-test",
        base_url="https://api.groq.com/openai/v1",
    )

    def mock_post(client, url, headers, json):
        return httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": "   "}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    with pytest.raises(AIProviderError, match="Groq returned empty content"):
        provider.complete_json(system="sys", user="usr")


def test_build_default_provider(monkeypatch):
    monkeypatch.setattr("app.config.settings.groq_api_key", None)
    assert build_default_provider() is None

    monkeypatch.setattr("app.config.settings.groq_api_key", "gsk-env-key")
    p = build_default_provider()
    assert p is not None
    assert p.is_configured() is True
