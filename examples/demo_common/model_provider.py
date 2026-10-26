# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Which model the example APIs call, read from the environment (and so from the ``.env``
files ``load_demo_env`` loads):

``MODEL_PROVIDER``   ``anthropic`` (default) or one of the OpenAI-compatible presets below
``MODEL_API_KEY``    the provider's key; each preset also reads its usual variable
``MODEL_BASE_URL``   overrides the preset's endpoint (required for ``openai-compatible``)
``MODEL_NAME``       the turn loop's model (required where the preset has none)
``MODEL_FAST_NAME``  memory extraction's model; defaults to ``MODEL_NAME``'s preset pair

``anthropic`` keeps the Anthropic SDK and its credential chain (``ANTHROPIC_API_KEY``);
``MODEL_BASE_URL`` points it at an Anthropic-compatible endpoint. Every other provider
runs through ``commerce_common.openai_compat``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic

from commerce_common.openai_compat import OpenAICompatibleClient


@dataclass(frozen=True)
class Preset:
    label: str
    base_url: str | None
    model: str | None
    fast_model: str | None
    key_env: str | None
    max_tokens_field: str = "max_tokens"
    key_required: bool = True


PRESETS: dict[str, Preset] = {
    "openai": Preset(
        "OpenAI",
        "https://api.openai.com/v1",
        "gpt-4.1",
        "gpt-4.1-mini",
        "OPENAI_API_KEY",
        max_tokens_field="max_completion_tokens",
    ),
    "deepseek": Preset(
        "DeepSeek",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
        "deepseek-chat",
        "DEEPSEEK_API_KEY",
    ),
    "qwen": Preset(
        "Qwen (DashScope)",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "qwen-turbo",
        "DASHSCOPE_API_KEY",
    ),
    "moonshot": Preset(
        "Moonshot",
        "https://api.moonshot.cn/v1",
        "moonshot-v1-32k",
        "moonshot-v1-8k",
        "MOONSHOT_API_KEY",
    ),
    "zhipu": Preset(
        "Zhipu GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-4-plus",
        "glm-4-flash",
        "ZHIPUAI_API_KEY",
    ),
    "ollama": Preset("Ollama", "http://localhost:11434/v1", None, None, None, key_required=False),
    "openai-compatible": Preset("the OpenAI-compatible endpoint", None, None, None, None),
}


@dataclass(frozen=True)
class ModelProvider:
    """The resolved settings. ``key_hint`` names where the key goes, for error messages."""

    name: str
    label: str
    api_key: str | None
    base_url: str | None
    model: str | None
    fast_model: str | None
    key_hint: str
    max_tokens_field: str = "max_tokens"
    key_required: bool = True

    @property
    def is_anthropic(self) -> bool:
        return self.name == "anthropic"

    def has_key(self) -> bool:
        if self.is_anthropic:
            return bool(
                self.api_key
                or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            )
        return bool(self.api_key) or not self.key_required


def _env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def resolve_provider() -> ModelProvider:
    """The provider the environment names. Raises ``ValueError`` for an unknown provider
    or a missing endpoint or model, so the API fails at startup rather than at chat."""
    name = (_env("MODEL_PROVIDER") or "anthropic").lower()
    if name == "anthropic":
        return ModelProvider(
            name=name,
            label="Anthropic",
            api_key=_env("MODEL_API_KEY"),
            base_url=_env("MODEL_BASE_URL"),
            model=_env("MODEL_NAME"),
            fast_model=_env("MODEL_FAST_NAME"),
            key_hint="ANTHROPIC_API_KEY (or MODEL_API_KEY)",
        )
    preset = PRESETS.get(name)
    if preset is None:
        known = ", ".join(["anthropic", *PRESETS])
        raise ValueError(f"MODEL_PROVIDER={name!r} is not one of: {known}")
    base_url = _env("MODEL_BASE_URL") or preset.base_url
    model = _env("MODEL_NAME") or preset.model
    if not base_url:
        raise ValueError(f"MODEL_PROVIDER={name} needs MODEL_BASE_URL")
    if not model:
        raise ValueError(f"MODEL_PROVIDER={name} needs MODEL_NAME")
    fast_model = _env("MODEL_FAST_NAME") or (preset.fast_model if not _env("MODEL_NAME") else model)
    key = _env("MODEL_API_KEY") or (_env(preset.key_env) if preset.key_env else None)
    hint = f"MODEL_API_KEY (or {preset.key_env})" if preset.key_env else "MODEL_API_KEY"
    return ModelProvider(
        name=name,
        label=preset.label,
        api_key=key,
        base_url=base_url,
        model=model,
        fast_model=fast_model,
        key_hint=hint,
        max_tokens_field=preset.max_tokens_field,
        key_required=preset.key_required,
    )


def model_client(timeout: float = 120.0) -> Any:
    """The client the agents take as ``client=``: None (the agent's own ``AsyncAnthropic``)
    unless a key or endpoint override applies, an ``AsyncAnthropic`` with them, or the
    OpenAI-compatible client."""
    provider = resolve_provider()
    if provider.is_anthropic:
        if not (provider.api_key or provider.base_url):
            return None
        return AsyncAnthropic(api_key=provider.api_key, base_url=provider.base_url, timeout=timeout)
    return OpenAICompatibleClient(
        base_url=provider.base_url or "",
        api_key=provider.api_key,
        timeout=timeout,
        max_tokens_field=provider.max_tokens_field,
        key_required=provider.key_required,
        provider_label=provider.label,
    )


def model_config_fields() -> dict[str, Any]:
    """The config fields a provider sets: the turn loop's model and the memory model,
    each only when the environment or the preset names one."""
    provider = resolve_provider()
    fields: dict[str, Any] = {}
    if provider.model:
        fields["model"] = provider.model
    if provider.fast_model:
        fields["memory_model"] = provider.fast_model
    return fields
