"""Smoke test for the Dynamo per-callsite telemetry helper.

Verifies the helper emits exactly one structured JSON line per call on
the ``lingo.dynamo`` logger at INFO. Real wiring to Dynamo callsites is
deferred — see app/db/dynamo/telemetry.py docstring.
"""

import json
import logging

from app.db.dynamo.telemetry import log_dynamo_op


def test_log_dynamo_op_emits_expected_json(caplog) -> None:
    caplog.set_level(logging.INFO, logger="lingo.dynamo")

    log_dynamo_op(
        table="lingo_social",
        operation="Query",
        callsite="social.router.list_friends",
    )

    records = [r for r in caplog.records if r.name == "lingo.dynamo"]
    assert len(records) == 1, f"expected one log record, got {len(records)}"
    payload = json.loads(records[0].getMessage())
    assert payload == {
        "table": "lingo_social",
        "op": "Query",
        "callsite": "social.router.list_friends",
    }


def test_log_dynamo_op_callsite_optional(caplog) -> None:
    """Callsite is optional — the line still logs without it."""
    caplog.set_level(logging.INFO, logger="lingo.dynamo")

    log_dynamo_op(table="lingo_users", operation="GetItem")

    records = [r for r in caplog.records if r.name == "lingo.dynamo"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload == {"table": "lingo_users", "op": "GetItem"}
    assert "callsite" not in payload


def test_log_dynamo_op_emits_valid_json_with_unicode(caplog) -> None:
    """Non-ASCII callsite strings should round-trip through json.loads."""
    caplog.set_level(logging.INFO, logger="lingo.dynamo")

    log_dynamo_op(
        table="lingo_decks",
        operation="PutItem",
        callsite="decks.router.upsert_日本語",
    )

    records = [r for r in caplog.records if r.name == "lingo.dynamo"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload["callsite"] == "decks.router.upsert_日本語"
