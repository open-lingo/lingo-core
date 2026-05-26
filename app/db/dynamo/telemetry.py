"""Per-callsite Dynamo operation logging.

AWS doesn't let you attach cost-allocation tags to *individual* API
calls — tags only flow through to billing at the resource level (one
tag set per table, not per request). So while
``lingo-infra/main.tf`` tags each ``aws_dynamodb_table`` with a
``Domain`` value (and Cost Explorer's ``/costs/by-domain`` rollup is
built from those tags), we can't tell from the bill whether a given
table's spend came from one chatty router or a hundred quiet ones.

What we CAN do is emit a structured log line on every Dynamo
operation and query CloudWatch Logs Insights to compute the per-
callsite distribution ourselves. That's what this module enables.

Usage::

    from app.db.dynamo.telemetry import log_dynamo_op

    async def list_friends(user_id: str) -> list[str]:
        log_dynamo_op(
            table="lingo_social",
            operation="Query",
            callsite="social.router.list_friends",
        )
        resp = await self._table.query(...)
        return ...

CloudWatch Logs Insights query::

    fields @timestamp, table, op, callsite
    | filter @logStream like /lingo-core/
    | stats count() by table, callsite
    | sort count desc

Output shape (one JSON-encoded line per call, written at INFO)::

    {"table": "lingo_social", "op": "Query", "callsite": "social.router.list_friends"}

Cost note: CloudWatch Logs ingestion is $0.50/GB and the JSON line is
~80 bytes; at 10k Dynamo ops/day that's ~24MB/month = ~$0.012. The
per-callsite visibility is worth the line-item.

⚠️ THIS HELPER IS NOT WIRED EVERYWHERE YET. Adding it to every Dynamo
callsite is a large refactor and is intentionally deferred. New Dynamo
work SHOULD call it; old callsites get backfilled opportunistically.
See ``CLAUDE.md`` → Cost telemetry.
"""

import json
import logging
from typing import Final

logger: Final = logging.getLogger("lingo.dynamo")


def log_dynamo_op(
    table: str,
    operation: str,
    callsite: str | None = None,
) -> None:
    """Emit a single structured JSON line for one Dynamo operation.

    Parameters
    ----------
    table:
        Physical Dynamo table name (e.g. ``"lingo_social"``). Match the
        Terraform resource exactly so CloudWatch Insights queries
        ``stats count() by table`` align with the cost-explorer rollup.
    operation:
        Boto3 op name as it appears on the wire — ``"GetItem"``,
        ``"Query"``, ``"PutItem"``, ``"UpdateItem"``, ``"BatchWriteItem"``,
        ``"TransactWriteItems"``. CloudWatch Insights groups by exact
        string, so misspellings split the histogram silently — prefer
        the boto3 method name verbatim.
    callsite:
        Dotted source location, e.g. ``"social.router.list_friends"``.
        Optional but strongly recommended — without it the line still
        logs but contributes nothing to the per-callsite breakdown,
        which is the whole point of this helper.
    """
    payload = {"table": table, "op": operation}
    if callsite is not None:
        payload["callsite"] = callsite
    # json.dumps keeps key order deterministic for stable CloudWatch
    # Insights `parse @message` predicates; ensure_ascii=False keeps
    # the line readable when callsite strings contain non-ASCII.
    logger.info(json.dumps(payload, ensure_ascii=False))
