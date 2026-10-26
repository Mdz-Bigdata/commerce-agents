# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The operations log the metrics report reads (``scripts/metrics_report.py``,
``docs/metrics.md``): one JSON line per chat turn, per feedback vote, per handoff to a
person, and per change the portal applies.

A turn record carries the model that produced the reply (``provider``, the name
``model_provider.resolve_provider`` resolved, and ``model``; never a key or an endpoint),
the evidence the turn rested on (the tools it called, the record ids their inputs named,
the source and version of each terms passage a read returned or withheld, how each call
ended, the gates that refused one), why it fell back, if it did (``fallback_reason``, read
from those events), and its outcome (stop reason, error kind, usage, latency, the writes
it made). No message text, tool result, or fact value is written: the log is for
counting, and ``docs/safety.md`` keeps conversation content out of logs below DEBUG.

Each role of each vertical writes its own file under ``<vertical>/data/.analytics/``
(``DEMO_ANALYTICS_DIR`` moves the directory; ``DEMO_ANALYTICS=0`` turns logging off).
"""

# The routes below take a ``CurrentSession`` annotation built at runtime, so this module
# evaluates its annotations eagerly (no ``from __future__ import annotations``).

import json
import logging
import os
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from commerce_common.streaming import AgentEvent
from commerce_common.turn import session_tag
from shopping_agent.gates import TERMS_GATE, WITHHELD

logger = logging.getLogger(__name__)

# Bump when a field's meaning changes; the report states the versions it read.
SCHEMA_VERSION = 1

# The reasons the UI sends; the handoff route may record ``rule_triggered`` in their place.
RequestedHandoffReason = Literal["user_requested", "after_negative_feedback", "after_error"]
HandoffReason = RequestedHandoffReason | Literal["rule_triggered"]

# Input keys whose values name a record: the turn's evidence ids.
_ID_KEYS = ("product_id", "listing_id", "change_id", "order_id", "campaign_id", "event_id")
_MAX_IDS = 20
# A model name as the settings panel accepts it (``ModelSettings.model``).
_MAX_MODEL = 120


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ids_in(value: Any, found: list[str]) -> None:
    if len(found) >= _MAX_IDS:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_KEYS or key.endswith("_ids"):
                for one in item if isinstance(item, list) else [item]:
                    if isinstance(one, str) and one not in found and len(found) < _MAX_IDS:
                        found.append(one[:80])
            elif isinstance(item, dict | list):
                _ids_in(item, found)
    elif isinstance(value, list):
        for item in value:
            _ids_in(item, found)


class TurnRecorder:
    """Watches one turn's events on their way to the client and writes its record when the
    turn ends. ``observe`` returns the event to send, with ``turn_id`` stamped on
    ``turn_complete`` so the client can vote on that turn."""

    def __init__(
        self,
        log: "OperationsLog",
        session_id: str,
        locale: str | None,
        *,
        model: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.log = log
        self.turn_id = f"t_{uuid.uuid4().hex[:16]}"
        self._record: dict[str, Any] = {
            "kind": "turn",
            "turn_id": self.turn_id,
            "session": session_tag(session_id),
            "locale": locale,
            "provider": provider,
            "model": model[:_MAX_MODEL] if model else None,
            "tools": [],
            "evidence_ids": [],
            "evidence_versions": [],
            "blocked": [],
            "writes": [],
            "error": None,
            "fallback_reason": None,
        }
        self._tools: dict[str, dict[str, Any]] = {}
        self._withheld = self._no_match = False
        log.remember(self.turn_id, session_id)

    def observe(self, event: AgentEvent) -> AgentEvent:
        data = event.data
        if event.type == "tool_call":
            entry = {"name": data.get("tool"), "status": "started"}
            self._tools[str(data.get("id"))] = entry
            self._record["tools"].append(entry)
            _ids_in(data.get("input"), self._record["evidence_ids"])
        elif event.type == "tool_result":
            entry = self._tools.get(str(data.get("id")))
            status = data.get("status") or ("error" if data.get("is_error") else "ok")
            if entry is not None:
                entry["status"] = status
            versions = self._record["evidence_versions"]
            for item in data.get("evidence") or []:
                if isinstance(item, dict) and item.get("status") in WITHHELD:
                    self._withheld = True
                if len(versions) < _MAX_IDS and isinstance(item, dict):
                    versions.append(
                        {
                            "source_id": str(item.get("source_id"))[:80],
                            "version": str(item.get("version"))[:20],
                            "status": item.get("status"),
                        }
                    )
            if data.get("no_match") and data.get("tool") == "search_policies":
                self._no_match = True
            if status == "blocked":
                self._record["blocked"].append(
                    {"tool": data.get("tool"), "reason": data.get("reason")}
                )
        elif event.type == "cart_update":
            self._record["writes"].append("cart")
        elif event.type == "change_update":
            change = data.get("change") or {}
            self._record["writes"].append(f"change:{change.get('status', 'staged')}")
        elif event.type == "turn_complete":
            self._record.update(
                stop_reason=data.get("stop_reason"),
                usage=data.get("usage"),
                elapsed_ms=data.get("elapsed_ms"),
            )
            return AgentEvent(type=event.type, data={**data, "turn_id": self.turn_id})
        return event

    def failed(self, kind: str) -> None:
        self._record["error"] = kind

    def fallback_reason(self) -> str | None:
        """Why the turn fell back, from its events alone: ``terms_withheld`` when a terms
        read withheld a source, ``no_terms_match`` when one found nothing,
        ``gate_held:<gate>`` when another gate held a call, else None."""
        if self._withheld:
            return "terms_withheld"
        if self._no_match:
            return "no_terms_match"
        for held in self._record["blocked"]:
            if held.get("reason") != TERMS_GATE:
                return f"gate_held:{held.get('reason')}"
        return None

    def write(self) -> None:
        self._record["fallback_reason"] = reason = self.fallback_reason()
        self.log.remember_fallback(self.turn_id, reason)
        self.log.write(self._record)


class OperationsLog:
    """One role's JSONL file. Writes are serialized and never raise: a full disk costs
    the metrics, not the chat."""

    def __init__(self, path: Path | None, *, vertical: str, role: str) -> None:
        self.path = path
        self.vertical = vertical
        self.role = role
        self._lock = threading.Lock()
        # turn_id -> session_id, so a vote or handoff is accepted only from the turn's session.
        self._turns: dict[str, str] = {}
        # turn_id -> fallback_reason, for the turns that fell back.
        self._fallbacks: dict[str, str] = {}

    @classmethod
    def for_example(cls, example_root: Path, role: str) -> "OperationsLog":
        if os.environ.get("DEMO_ANALYTICS", "1") == "0":
            return cls(None, vertical=example_root.name, role=role)
        directory = Path(
            os.environ.get("DEMO_ANALYTICS_DIR") or example_root / "data" / ".analytics"
        )
        return cls(directory / f"{role}.jsonl", vertical=example_root.name, role=role)

    def remember(self, turn_id: str, session_id: str) -> None:
        self._turns[turn_id] = session_id

    def owns(self, turn_id: str, session_id: str) -> bool:
        return self._turns.get(turn_id) == session_id

    def remember_fallback(self, turn_id: str, reason: str | None) -> None:
        if reason is not None:
            self._fallbacks[turn_id] = reason

    def handoff_reason(
        self, turn_id: str | None, requested: RequestedHandoffReason
    ) -> HandoffReason:
        """The reason a handoff is recorded under: ``rule_triggered`` when the turn it was
        opened from fell back (a withheld or missing term, a held call) and the UI saw no
        system error, else the reason the UI sent."""
        if turn_id in self._fallbacks and requested != "after_error":
            return "rule_triggered"
        return requested

    def turn(
        self,
        session_id: str,
        locale: str | None,
        *,
        model: str | None = None,
        provider: str | None = None,
    ) -> TurnRecorder:
        """A recorder for one turn; ``model`` is the name the turn loop calls and
        ``provider`` the resolved provider's name (``anthropic``, ``deepseek``, ...)."""
        return TurnRecorder(self, session_id, locale, model=model, provider=provider)

    def feedback(self, session_id: str, turn_id: str, helpful: bool) -> None:
        self.write(
            {
                "kind": "feedback",
                "turn_id": turn_id,
                "session": session_tag(session_id),
                "helpful": helpful,
            }
        )

    def handoff(self, session_id: str, turn_id: str | None, reason: HandoffReason) -> str:
        ticket = f"H-{uuid.uuid4().hex[:6].upper()}"
        self.write(
            {
                "kind": "handoff",
                "turn_id": turn_id,
                "session": session_tag(session_id),
                "reason": reason,
                "ticket": ticket,
            }
        )
        return ticket

    def action(self, session_id: str, name: str, ids: Iterable[str] = ()) -> None:
        self.write(
            {"kind": "action", "session": session_tag(session_id), "name": name, "ids": list(ids)}
        )

    def write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        line = json.dumps(
            {
                "v": SCHEMA_VERSION,
                "ts": _now(),
                "vertical": self.vertical,
                "role": self.role,
                **record,
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            logger.warning("operations log write failed: %s", self.path, exc_info=True)


class FeedbackRequest(BaseModel):
    turn_id: str = Field(min_length=1, max_length=40)
    helpful: bool


class HandoffRequest(BaseModel):
    turn_id: str | None = Field(default=None, max_length=40)
    reason: RequestedHandoffReason = "user_requested"


def install_operations_routes(
    router: APIRouter | FastAPI,
    prefix: str,
    *,
    current_session: Any,
    log: OperationsLog,
    handoff_note: str,
) -> None:
    """``POST {prefix}/feedback`` records a vote on one of the session's turns;
    ``POST {prefix}/handoff`` opens a ticket for a person and returns it, and queues
    ``handoff_note`` (``{ticket}``) for the model's next turn as an app event; the ticket's
    reason is ``OperationsLog.handoff_reason``."""

    @router.post(f"{prefix}/feedback")
    async def feedback(request: FeedbackRequest, record: current_session) -> dict:
        if not log.owns(request.turn_id, record.session_id):
            raise HTTPException(status_code=404, detail="No such turn in this session")
        log.feedback(record.session_id, request.turn_id, request.helpful)
        return {"ok": True}

    @router.post(f"{prefix}/handoff")
    async def handoff(request: HandoffRequest, record: current_session) -> dict:
        if request.turn_id is not None and not log.owns(request.turn_id, record.session_id):
            raise HTTPException(status_code=404, detail="No such turn in this session")
        reason = log.handoff_reason(request.turn_id, request.reason)
        ticket = log.handoff(record.session_id, request.turn_id, reason)
        record.pending_app_events.append(handoff_note.format(ticket=ticket))
        return {"ok": True, "ticket": ticket}
