"""Async-event publisher — fire-and-forget into the lingo-events SQS queue.

Consumer: ``lingo-async`` (separate repo / Lambda). Contract: see
``../../../lingo-async/app/contracts/messages.py``.

Design notes:
  - Queue URL comes from ``EVENTS_QUEUE_URL``. Unset ⇒ ``publish`` logs
    + no-ops, so local dev without SQS still works. The producer-side
    smoke test for "did the publish call happen?" is a log line.
  - boto3 client is instantiated at MODULE level (cold-start cost),
    never per-request. Lambda containers reuse it across invocations.
  - Errors are logged + swallowed. A failing publish must NEVER take
    down a lesson sync — the user-facing path is the customer; the
    event is a downstream nice-to-have. If the queue's down for long
    enough that we care, CloudWatch alarms on SQS itself catch it.
"""

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger("lingo.events")

# Single client per warm container. Lambda sets AWS_REGION automatically
# at runtime; local dev should set it via .env (Pydantic Settings won't
# help here — this module is intentionally NOT wired through
# app.config so the publisher stays a leaf with no domain imports).
_sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "us-west-1"))

_QUEUE_URL = os.environ.get("EVENTS_QUEUE_URL", "")


def publish(event: dict[str, Any]) -> None:
    """Send one event to the lingo-events queue.

    ``event`` must be a JSON-serialisable dict matching one of the
    ``EventMessage`` shapes in lingo-async. The producer is responsible
    for setting ``type`` + ``version`` correctly; we don't validate
    here (Pydantic on the consumer side is the single source of truth
    for the schema).

    No-ops when ``EVENTS_QUEUE_URL`` is unset (local dev or a deploy
    that hasn't been wired to SQS yet).
    """
    if not _QUEUE_URL:
        logger.debug("event_publish_skipped reason=no_queue_url type=%s", event.get("type"))
        return

    try:
        body = json.dumps(event, default=str)
        _sqs.send_message(QueueUrl=_QUEUE_URL, MessageBody=body)
        logger.debug("event_published type=%s", event.get("type"))
    except Exception as exc:  # noqa: BLE001 — publish failures never break the caller
        logger.warning("event_publish_failed type=%s err=%s", event.get("type"), exc)
