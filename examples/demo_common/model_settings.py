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
        "has_key": bool(provider and provider.has_key()),
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
        try:
            provider = resolve_provider()
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        if not provider.has_key() and provider.key_required:
            return {"ok": False, "error": f"No API key is set for {provider.label}."}
        model = _test_model(provider)
        try:
            await provider_client(provider, timeout=30.0).messages.create(
                model=model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with the word ok."}],
            )
        except (anthropic.AuthenticationError, ModelAuthenticationError):
            return {"ok": False, "model": model, "error": f"{provider.label} rejected the API key."}
        except (anthropic.APIStatusError, ModelProviderError) as failure:
            status = getattr(failure, "status_code", None) or getattr(failure, "status", None)
            return {
                "ok": False,
                "model": model,
                "error": f"{provider.label} returned an error ({status or 'no status'}); "
                "check the model name and endpoint.",
            }
        except Exception:  # a connection failure or anything else: the log keeps the detail
            logger.exception("model settings test failed")
            return {
                "ok": False,
                "model": model,
                "error": f"Could not reach {provider.label}; check the endpoint.",
            }
        return {"ok": True, "model": model, "label": provider.label}
