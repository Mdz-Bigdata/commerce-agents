# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The operations log the metrics report reads (``scripts/metrics_report.py``,
``docs/metrics.md``): one JSON line per chat turn, per feedback vote, per handoff to a
person, and per change the portal applies.

A turn record carries the evidence the turn rested on (the tools it called, the record ids
their inputs named, how each call ended, the gates that refused one) and its outcome (stop
reason, error kind, usage, latency, the writes it made). No message text, tool result, or
fact value is written: the log is for counting, and ``docs/safety.md`` keeps conversation
content out of logs below DEBUG.

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

logger = logging.getLogger(__name__)

# Bump when a field's meaning changes; the report states the versions it read.
SCHEMA_VERSION = 1

HandoffReason = Literal["user_requested", "after_negative_feedback", "after_error"]

# Input keys whose values name a record: the turn's evidence ids.
_ID_KEYS = ("product_id", "listing_id", "change_id", "order_id", "campaign_id", "event_id")
_MAX_IDS = 20


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

    def __init__(self, log: "OperationsLog", session_id: str, locale: str | None) -> None:
        self.log = log
        self.turn_id = f"t_{uuid.uuid4().hex[:16]}"
        self._record: dict[str, Any] = {
            "kind": "turn",
            "turn_id": self.turn_id,
            "session": session_tag(session_id),
            "locale": locale,
            "tools": [],
            "evidence_ids": [],
            "blocked": [],
            "writes": [],
            "error": None,
        }
        self._tools: dict[str, dict[str, Any]] = {}
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

    def write(self) -> None:
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

    def turn(self, session_id: str, locale: str | None) -> TurnRecorder:
        return TurnRecorder(self, session_id, locale)

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
    reason: HandoffReason = "user_requested"


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
    ``handoff_note`` (``{ticket}``) for the model's next turn as an app event."""

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
        ticket = log.handoff(record.session_id, request.turn_id, request.reason)
        record.pending_app_events.append(handoff_note.format(ticket=ticket))
        return {"ok": True, "ticket": ticket}
