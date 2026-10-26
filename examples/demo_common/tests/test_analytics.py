# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from commerce_common.streaming import AgentEvent
from demo_common import SESSION_HEADER, SessionStore, session_dependency
from demo_common.analytics import OperationsLog, install_operations_routes
from shopping_agent import ShoppingSessionState


def lines(log: OperationsLog) -> list[dict]:
    assert log.path is not None
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_a_turn_record_holds_evidence_and_outcome_but_no_text(tmp_path):
    log = OperationsLog(tmp_path / "storefront.jsonl", vertical="retail", role="storefront")
    recorder = log.turn("session-1", "zh")
    events = [
        AgentEvent.text_delta("A private sentence."),
        AgentEvent.tool_call("get_product_details", "tu-1", {"product_id": "AR-1"}),
        AgentEvent.tool_result("get_product_details", "tu-1", "ok"),
        AgentEvent.tool_call("add_to_cart", "tu-2", {"product_id": "AR-9", "quantity": 1}),
        AgentEvent.tool_result(
            "add_to_cart", "tu-2", "held", status="blocked", reason="provenance"
        ),
        AgentEvent.cart_update({"items": []}),
        AgentEvent.turn_complete("end_turn", {"input_tokens": 9}, 1200, 0),
    ]
    sent = [recorder.observe(event) for event in events]
    recorder.write()
    assert sent[-1].data["turn_id"] == recorder.turn_id
    (record,) = lines(log)
    assert record["kind"] == "turn" and record["locale"] == "zh" and record["vertical"] == "retail"
    assert record["evidence_ids"] == ["AR-1", "AR-9"]
    assert [tool["status"] for tool in record["tools"]] == ["ok", "blocked"]
    assert record["blocked"] == [{"tool": "add_to_cart", "reason": "provenance"}]
    assert record["writes"] == ["cart"] and record["elapsed_ms"] == 1200
    assert "private" not in json.dumps(record) and "session-1" not in json.dumps(record)


def test_a_terms_read_logs_the_version_of_each_source(tmp_path):
    log = OperationsLog(tmp_path / "storefront.jsonl", vertical="retail", role="storefront")
    recorder = log.turn("session-1", None)
    evidence = [
        {"source_id": "retail/returns", "version": "1", "status": "superseded"},
        {"source_id": "retail/returns", "version": "2", "status": "current"},
    ]
    recorder.observe(AgentEvent.tool_call("search_policies", "tu-1", {"query": "returns"}))
    recorder.observe(AgentEvent.tool_result("search_policies", "tu-1", "ok", evidence=evidence))
    recorder.write()
    (record,) = lines(log)
    assert record["evidence_versions"] == evidence


def test_votes_and_handoffs_are_accepted_only_for_the_sessions_own_turns(tmp_path):
    log = OperationsLog(tmp_path / "storefront.jsonl", vertical="retail", role="storefront")
    store: SessionStore[ShoppingSessionState] = SessionStore(ShoppingSessionState)
    current = session_dependency(store, "/api/session")
    app = FastAPI()
    install_operations_routes(
        app, "/api", current_session=current, log=log, handoff_note="ticket {ticket}"
    )
    client = TestClient(app)
    mine, other = store.start("u-1"), store.start("u-2")
    turn_id = log.turn(mine.session_id, None).turn_id
    headers = {SESSION_HEADER: mine.session_id}

    vote = client.post(
        "/api/feedback", json={"turn_id": turn_id, "helpful": False}, headers=headers
    )
    assert vote.status_code == 200
    stranger = {SESSION_HEADER: other.session_id}
    assert (
        client.post(
            "/api/feedback", json={"turn_id": turn_id, "helpful": True}, headers=stranger
        ).status_code
        == 404
    )
    assert (
        client.post("/api/feedback", json={"turn_id": turn_id, "helpful": True}).status_code == 401
    )

    handoff = client.post(
        "/api/handoff",
        json={"turn_id": turn_id, "reason": "after_negative_feedback"},
        headers=headers,
    )
    ticket = handoff.json()["ticket"]
    assert ticket.startswith("H-")
    assert store.require(mine.session_id).pending_app_events == [f"ticket {ticket}"]
    kinds = [(record["kind"], record.get("reason")) for record in lines(log)]
    assert kinds == [("feedback", None), ("handoff", "after_negative_feedback")]


def test_logging_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_ANALYTICS", "0")
    log = OperationsLog.for_example(tmp_path / "retail", "storefront")
    log.feedback("s", "t", True)
    assert log.path is None and not (tmp_path / "retail").exists()
