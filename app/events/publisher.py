"""Async-event publisher — transport-agnostic via kombu.

Consumer: ``lingo-async`` (separate repo / Lambda). Contract: see
``../../../lingo-async/app/contracts/messages.py``.

Transport is chosen by the ``EVENTS_BROKER_URL`` env var:
  - ``sqs://``                — production. kombu uses boto3 underneath
    and picks up the runtime IAM role. Optional creds can be embedded
    (``sqs://AKIA...:SECRET@``) for non-Lambda hosts.
  - ``redis://localhost:6379/0`` — local dev. ``lingo-async`` runs as a
    kombu ConsumerMixin against the same broker (see
    ``lingo-async/app/local_consumer.py``).
  - ``memory://``              — single-process tests.

Same Exchange + Queue config on both sides. Same ``publish()`` call.
Local dev fires the consumer side-effects (quest progress, leaderboard
updates) without LocalStack / Docker SQS.

Design notes:
  - Connection is opened per publish() call. Cheap for the volumes here
    (one publish per attempt batch / per XP award). A long-lived
    connection pool is the right answer if call volume jumps.
  - Errors are logged + swallowed. A failing publish must NEVER take
    down a lesson sync — the user-facing path is the customer; the
    event is a downstream nice-to-have.
"""

import logging
import os
from typing import Any

from kombu import Connection, Exchange, Producer, Queue

logger = logging.getLogger("lingo.events")

# Shared transport config — match exactly with lingo-async's consumer.
# Direct exchange + single queue is intentional: we don't fan-out today,
# and the async worker is the only consumer.
EVENTS_EXCHANGE = Exchange("lingo-events", type="direct", durable=True)
EVENTS_QUEUE = Queue("lingo-events", EVENTS_EXCHANGE, routing_key="events", durable=True)

_BROKER_URL = os.environ.get("EVENTS_BROKER_URL", "")


def publish(event: dict[str, Any]) -> None:
    """Publish one event. JSON-serialisable dict matching one of the
    ``EventMessage`` shapes in lingo-async.

    No-ops when ``EVENTS_BROKER_URL`` is unset (local dev without a
    broker, or a deploy not yet wired to async events).
    """
    if not _BROKER_URL:
        logger.debug("event_publish_skipped reason=no_broker_url type=%s", event.get("type"))
        return

    try:
        with Connection(_BROKER_URL) as conn:
            producer = Producer(conn, exchange=EVENTS_EXCHANGE, routing_key="events")
            producer.publish(
                event,
                serializer="json",
                declare=[EVENTS_QUEUE],
                retry=True,
                retry_policy={"interval_start": 0, "interval_step": 0.2, "max_retries": 2},
            )
        logger.debug("event_published type=%s", event.get("type"))
    except Exception as exc:  # noqa: BLE001 — publish failures never break the caller
        logger.warning("event_publish_failed type=%s err=%s", event.get("type"), exc)
