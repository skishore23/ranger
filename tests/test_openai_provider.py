import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.llm.provider import OpenAIProvider


@pytest.fixture
def fake_openai_module():
    """Inject a fake openai module that records how it's used."""

    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Generated text"))]
    )

    module = SimpleNamespace(OpenAI=lambda api_key: client)
    original = sys.modules.get("openai")
    sys.modules["openai"] = module
    try:
        yield client
    finally:
        if original is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original


def test_openai_provider_initialization(fake_openai_module):
    provider = OpenAIProvider(api_key="test_api_key")
    assert provider.client is fake_openai_module


def test_openai_provider_uses_environment_variable(monkeypatch):
    client = Mock()
    module = SimpleNamespace(OpenAI=lambda api_key: client)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")

    provider = OpenAIProvider(api_key=None)

    assert provider.client is client


def test_openai_provider_missing_api_key(monkeypatch):
    module = SimpleNamespace(OpenAI=lambda api_key: Mock())
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY environment variable not set"):
        OpenAIProvider(api_key=None)


def test_generate_text_success(fake_openai_module):
    provider = OpenAIProvider(api_key="test_api_key")
    result = provider.generate(prompt="Hello", model="gpt-4o-mini")

    assert result == "Generated text"
    fake_openai_module.chat.completions.create.assert_called_once()


def test_generate_text_schema_requests_json(monkeypatch):
    recorded_kwargs = {}

    def create(**kwargs):
        recorded_kwargs.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"tests": []}'))]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    module = SimpleNamespace(OpenAI=lambda api_key: client)
    monkeypatch.setitem(sys.modules, "openai", module)

    provider = OpenAIProvider(api_key="key")
    result = provider.generate(
        prompt="Hello",
        model="gpt-4o-mini",
        schema={"type": "object", "properties": {"tests": {"type": "array"}}},
    )

    assert result == '{"tests": []}'
    assert recorded_kwargs["response_format"] == {"type": "json_object"}
