"""Client-error ingestion — the in-house alternative to a vendor crash
reporter (Sentry/Bugsnag/etc. stayed a Spencer decision; this is the
zero-vendor path). See `../lingo/docs/observability-2026-09-17.md` for the
full contract.

No auth: errors happen before login too (boot failures, the login screen
itself throwing), so this can't sit behind `get_acting_user`. Protected
instead by `TelemetryGuardMiddleware` (body-size cap + per-IP token
bucket, registered in `app/main.py`) and by the hard per-item/per-batch
caps in `app/telemetry/schemas.py`.
"""

import json
import logging
import secrets

from fastapi import APIRouter, Request, Response

from app.shared.request_id import get_request_id
from app.telemetry.schemas import (
    ClientDiagnosticsAcceptedResponse,
    ClientDiagnosticsDocument,
    ClientErrorAcceptedResponse,
    ClientErrorBatch,
)

router = APIRouter(tags=["telemetry"])

# Dedicated logger/name so a CloudWatch Logs Insights query can filter to
# exactly this stream (`filter @logStream like /lingo-core/` +
# `fields @message | filter message like /"type":"client_error"/` also
# works, but a distinct logger name is the cheaper filter). See the doc for
# the actual Insights query.
logger = logging.getLogger("lingo.client_error")

# Separate logger name for the diagnostics dump — same JSON-line convention,
# but a distinct stream so a Logs Insights query for one never has to
# exclude the other by field-shape guesswork.
diag_logger = logging.getLogger("lingo.client_diag")

# Excludes 0/O/1/I on purpose (task spec: "unambiguous alphabet") — this
# code gets read aloud/typed by hand off a phone screen ("Tell Spencer:
# K7P4QX"), so visually-confusable characters are worse than a slightly
# smaller alphabet. 32 chars ^ 6 positions = ~1.07e9 possible codes; not a
# security boundary (CloudWatch retention + the pull script are the real
# access control), just collision-unlikely enough for a human-readable
# lookup key.
DIAGNOSTICS_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
DIAGNOSTICS_CODE_LENGTH = 6


def _generate_diagnostics_code() -> str:
    return "".join(secrets.choice(DIAGNOSTICS_CODE_ALPHABET) for _ in range(DIAGNOSTICS_CODE_LENGTH))


@router.post("/errors", response_model=ClientErrorAcceptedResponse, status_code=202)
async def report_client_errors(
    batch: ClientErrorBatch,
    request: Request,
    response: Response,
) -> ClientErrorAcceptedResponse:
    request_id = get_request_id(request)
    response.headers["X-Request-Id"] = request_id

    for item in batch.items:
        # One JSON line per item (matches `app/db/dynamo/telemetry.py`'s
        # `log_dynamo_op` convention: `logger.<level>(json.dumps(payload))`,
        # not the stdlib `extra=` pattern — this repo's structured-logging
        # convention is a JSON-encoded message, not LogRecord extras, so
        # CloudWatch Insights `fields @message | parse @message` style
        # queries work without a custom formatter).
        payload = {
            "type": "client_error",
            "requestId": request_id,
            "message": item.message,
            "stack": item.stack,
            "source": item.source,
            "route": item.route,
            "lessonId": item.lessonId,
            "stepIndex": item.stepIndex,
            "stepType": item.stepType,
            "appVersion": item.appVersion,
            "buildNumber": item.buildNumber,
            "platform": item.platform,
            "osVersion": item.osVersion,
            "fontScale": item.fontScale,
            "online": item.online,
            "count": item.count,
            "clientTs": item.ts,
            "sessionId": item.sessionId,
            "lastRequestId": item.lastRequestId,
            "breadcrumbs": [b.model_dump() for b in item.breadcrumbs] if item.breadcrumbs else None,
        }
        # WARNING, not INFO/ERROR: these are real client-side failures (worth
        # alarming on in aggregate) but never take the service down, and a
        # 500-vs-warning split would ambiguously overlap with THIS request's
        # own 5xx semantics if something above ever misfires.
        logger.warning(json.dumps(payload, ensure_ascii=False))

    return ClientErrorAcceptedResponse(accepted=len(batch.items))


@router.post("/diagnostics", response_model=ClientDiagnosticsAcceptedResponse, status_code=202)
async def report_client_diagnostics(
    doc: ClientDiagnosticsDocument,
    request: Request,
    response: Response,
) -> ClientDiagnosticsAcceptedResponse:
    """One-tap "Send diagnostics" (Sync panel, next to the Layout-trace
    tools). No auth, same rationale as `/errors` above; guarded by the same
    `TelemetryGuardMiddleware` (150 KB body cap, shared per-IP token
    bucket — see `guard.py`).

    Logs ONE structured `lingo.client_diag` line, keyed by a fresh
    server-generated code so Spencer can say "K7P4QX" out loud and
    `scripts/ops/pull-diagnostics.mjs K7P4QX` (in the `lingo` repo) greps
    it straight back out of CloudWatch — no request-id hunting, no log
    stream browsing.
    """
    request_id = get_request_id(request)
    response.headers["X-Request-Id"] = request_id
    code = _generate_diagnostics_code()

    payload = {
        "type": "client_diag",
        "code": code,
        "requestId": request_id,
        "sessionLog": [e.model_dump() for e in doc.sessionLog],
        "layoutTrace": doc.layoutTrace,
        "tapReplay": doc.tapReplay,
        "device": doc.device.model_dump(),
        "lastRequestId": doc.lastRequestId,
    }
    # WARNING, matching /errors' own level choice above — a diagnostics dump
    # is diagnostic signal, not a service failure, but WARNING keeps it out
    # of default INFO-suppressed log views the way the error line is.
    diag_logger.warning(json.dumps(payload, ensure_ascii=False))

    return ClientDiagnosticsAcceptedResponse(code=code)
