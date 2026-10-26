# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from commerce_common.openai_compat import OpenAICompatibleClient
from demo_common.host import chat_error_text, reply_language
from demo_common.model_provider import (
    model_client,
    model_config_fields,
    resolve_provider,
)

VARIABLES = (
    "MODEL_PROVIDER",
    "MODEL_API_KEY",
    "MODEL_BASE_URL",
    "MODEL_NAME",
    "MODEL_FAST_NAME",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_anthropic_is_the_default_and_keeps_the_agents_own_client():
    provider = resolve_provider()
    assert provider.is_anthropic and not provider.has_key()
    assert model_client() is None
    assert model_config_fields() == {}


def test_a_preset_supplies_endpoint_models_and_its_usual_key(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    provider = resolve_provider()
    assert provider.has_key() and provider.base_url.startswith("https://api.deepseek.com")
    client = model_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert model_config_fields() == {"model": "deepseek-chat", "memory_model": "deepseek-chat"}


def test_model_name_overrides_both_models_unless_a_fast_one_is_named(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "big")
    assert model_config_fields() == {"model": "big", "memory_model": "big"}
    monkeypatch.setenv("MODEL_FAST_NAME", "small")
    assert model_config_fields()["memory_model"] == "small"
    assert resolve_provider().max_tokens_field == "max_completion_tokens"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MODEL_PROVIDER": "nope"}, "is not one of"),
        ({"MODEL_PROVIDER": "openai-compatible"}, "needs MODEL_BASE_URL"),
        ({"MODEL_PROVIDER": "ollama"}, "needs MODEL_NAME"),
    ],
)
def test_incomplete_settings_fail_at_startup(monkeypatch, env, message):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        resolve_provider()


def test_chat_errors_name_the_provider_in_the_requested_language(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    provider = resolve_provider()
    english = chat_error_text("no_key", "en", provider, "examples/retail/.env")
    chinese = chat_error_text("no_key", "zh", provider, "examples/retail/.env")
    assert "DeepSeek" in english and "DEEPSEEK_API_KEY" in english
    assert "DeepSeek" in chinese and "密钥" in chinese
    assert reply_language("zh") == "Simplified Chinese" and reply_language(None) is None


def test_the_reply_language_reaches_both_context_blocks():
    from merchant_agent.prompt import build_dynamic_context as merchant_context
    from shopping_agent.prompt import build_dynamic_context as shopping_context

    shopping = shopping_context(
        preferences=None, memory_facts=[], cart=None, page=None, reply_language="Simplified Chinese"
    )
    merchant = merchant_context(
        merchant_context=None, memory_facts=[], reply_language="Simplified Chinese"
    )
    assert '"reply_language": "Simplified Chinese"' in shopping
    assert '"reply_language": "Simplified Chinese"' in merchant
    assert "reply_language" not in shopping_context(
        preferences=None, memory_facts=[], cart=None, page=None
    )
