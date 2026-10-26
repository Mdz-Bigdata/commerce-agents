# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The model settings routes the web apps' settings panel calls, on every example API:

``GET    /api/model-settings``        the provider in force, the presets, whether a key is set
``PUT    /api/model-settings``        save provider, endpoint, model names, and optionally a key
``DELETE /api/model-settings``        drop the saved settings; the ``.env`` values apply again
``POST   /api/model-settings/test``   one short model call under the settings in force

A key goes in and never comes back out: reads say only whether one is set and its last
four characters when the panel saved it. The settings live in ``model_provider``'s file
and apply to the next turn without a restart. The APIs answer only loopback host names
(``build_app``); ``DEMO_MODEL_SETTINGS=0`` makes the settings read-only, for a deployment
whose model is fixed by its environment.
"""

# The routes take FastAPI-resolved bodies, so annotations are evaluated eagerly (no
# ``from __future__ import annotations``).

import logging
import os
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from commerce_common.openai_compat import ModelAuthenticationError, ModelProviderError

from .model_provider import (
    PRESETS,
    ModelProvider,
    ModelSettings,
    clear_settings,
    provider_client,
    resolve_provider,
    save_settings,
    saved_settings,
)

logger = logging.getLogger(__name__)

PREFIX = "/api/model-settings"


class SettingsUpdate(BaseModel):
    """A save from the panel. ``api_key`` left out (or null) keeps the key already saved;
    an empty string removes it."""

    provider: str = Field(min_length=1, max_length=40)
    api_key: str | None = Field(default=None, max_length=400)
    base_url: str | None = Field(default=None, max_length=400)
    model: str | None = Field(default=None, max_length=120)
    fast_model: str | None = Field(default=None, max_length=120)


def editable() -> bool:
    return os.environ.get("DEMO_MODEL_SETTINGS", "1") != "0"


def _presets() -> list[dict[str, Any]]:
    rows = [
        {
            "name": "anthropic",
            "label": "Anthropic",
            "base_url": None,
            "model": None,
            "fast_model": None,
            "key_required": True,
        }
    ]
    rows += [
        {
            "name": name,
            "label": preset.label,
            "base_url": preset.base_url,
            "model": preset.model,
            "fast_model": preset.fast_model,
            "key_required": preset.key_required,
        }
        for name, preset in PRESETS.items()
    ]
    return rows


def settings_payload() -> dict[str, Any]:
    """What the panel shows: never the key itself."""
    try:
        provider: ModelProvider | None = resolve_provider()
        problem = None
    except ValueError as error:
        provider, problem = None, str(error)
    saved = saved_settings()
    saved_key = saved.api_key if saved is not None else None
    return {
        "provider": provider.name if provider else None,
        "label": provider.label if provider else None,
        "base_url": provider.base_url if provider else None,
        "model": provider.model if provider else None,
        "fast_model": provider.fast_model if provider else None,
        # A key the provider does not need is reported as not set, beside key_required.
        "has_key": bool(
            provider and provider.has_key() and (provider.api_key or provider.is_anthropic)
        ),
        "key_required": provider.key_required if provider else True,
        "key_hint": provider.key_hint if provider else None,
        "key_preview": f"…{saved_key[-4:]}" if saved_key and len(saved_key) > 8 else None,
        "source": provider.source if provider else ("page" if saved else "env"),
        "problem": problem,
        "editable": editable(),
        "presets": _presets(),
    }


# resolve_provider names the environment variables; the panel names its own fields.
_PANEL_WORDING = {
    "needs MODEL_BASE_URL": "This provider needs an endpoint.",
    "needs MODEL_NAME": "This provider needs a model name.",
    "is not one of": "Choose a provider from the list.",
}


def _panel_wording(message: str) -> str:
    return next((text for marker, text in _PANEL_WORDING.items() if marker in message), message)


def _test_model(provider: ModelProvider) -> str:
    if provider.model:
        return provider.model
    from shopping_agent import ShoppingAgentConfig

    return ShoppingAgentConfig.model_fields["model"].default


def install_model_settings_routes(app: FastAPI) -> None:
    @app.get(PREFIX)
    async def read_settings() -> dict:
        return settings_payload()

    @app.put(PREFIX)
    async def write_settings(update: SettingsUpdate) -> dict:
        if not editable():
            raise HTTPException(status_code=403, detail="Model settings are read-only here")
        saved = saved_settings()
        key = update.api_key
        if key is None and saved is not None and saved.provider == update.provider.lower():
            key = saved.api_key
        settings = ModelSettings(
            provider=update.provider,
            api_key=key or None,
            base_url=update.base_url,
            model=update.model,
            fast_model=update.fast_model,
        )
        try:
            provider = save_settings(settings)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=_panel_wording(str(error))) from error
        logger.info("model settings saved: %s, model %s", provider.label, provider.model)
        return settings_payload()

    @app.delete(PREFIX)
    async def reset_settings() -> dict:
        if not editable():
            raise HTTPException(status_code=403, detail="Model settings are read-only here")
        clear_settings()
        return settings_payload()

    @app.post(f"{PREFIX}/test")
    async def test_settings() -> dict:
        """``ok`` with the model that answered, or a ``code`` the panel words in the page's
        language (``invalid``, ``no_key``, ``auth``, ``status``, ``unreachable``) plus an
        English ``error``."""
        try:
            provider = resolve_provider()
        except ValueError as error:
            return {"ok": False, "code": "invalid", "error": _panel_wording(str(error))}
        label = provider.label
        if not provider.has_key() and provider.key_required:
            return {
                "ok": False,
                "code": "no_key",
                "label": label,
                "error": f"No API key is set for {label}.",
            }
        model = _test_model(provider)
        failure: dict[str, Any] | None = None
        try:
            await provider_client(provider, timeout=30.0).messages.create(
                model=model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with the word ok."}],
            )
        except (anthropic.AuthenticationError, ModelAuthenticationError):
            failure = {"code": "auth", "error": f"{label} rejected the API key."}
        except (anthropic.APIStatusError, ModelProviderError) as error:
            status = getattr(error, "status_code", None) or getattr(error, "status", None)
            failure = {
                "code": "status",
                "status": status,
                "error": f"{label} returned an error ({status or 'no status'}); "
                "check the model name and endpoint.",
            }
        except Exception:  # a connection failure or anything else: the log keeps the detail
            logger.exception("model settings test failed")
            failure = {
                "code": "unreachable",
                "error": f"Could not reach {label}; check the endpoint.",
            }
        if failure is not None:
            return {"ok": False, "label": label, "model": model, **failure}
        return {"ok": True, "label": label, "model": model}
