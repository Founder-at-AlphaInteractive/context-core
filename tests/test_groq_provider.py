import pytest

from app.providers.base import AIProvider, AIProviderError
from app.providers.groq_provider import GroqProvider


def test_groq_provider_requires_api_key():
    p = GroqProvider(api_key="", model="m", base_url="https://example.com")
    assert p.is_configured() is False
    with pytest.raises(AIProviderError):
        p.complete_json(system="s", user="u")


def test_groq_provider_handles_http_error(monkeypatch):
    import httpx

    class FakeResponse:
        status_code = 500
        text = "server error"

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    p = GroqProvider(
        api_key="fake-key", model="m", base_url="https://example.com"
    )
    with pytest.raises(AIProviderError):
        p.complete_json(system="s", user="u")


def test_groq_provider_returns_content(monkeypatch):
    import httpx

    class FakeResponse:
        status_code = 200
        text = "ok"

        def json(self):
            return {
                "choices": [
                    {"message": {"content": '{"items": []}'}}
                ]
            }

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    p = GroqProvider(
        api_key="fake-key", model="m", base_url="https://example.com"
    )
    out = p.complete_json(system="s", user="u")
    assert out == '{"items": []}'
