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

from fastapi import APIRouter, Request, Response

from app.shared.request_id import get_request_id
from app.telemetry.schemas import ClientErrorAcceptedResponse, ClientErrorBatch

router = APIRouter(tags=["telemetry"])

# Dedicated logger/name so a CloudWatch Logs Insights query can filter to
# exactly this stream (`filter @logStream like /lingo-core/` +
# `fields @message | filter message like /"type":"client_error"/` also
# works, but a distinct logger name is the cheaper filter). See the doc for
# the actual Insights query.
logger = logging.getLogger("lingo.client_error")


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
        }
        # WARNING, not INFO/ERROR: these are real client-side failures (worth
        # alarming on in aggregate) but never take the service down, and a
        # 500-vs-warning split would ambiguously overlap with THIS request's
        # own 5xx semantics if something above ever misfires.
        logger.warning(json.dumps(payload, ensure_ascii=False))

    return ClientErrorAcceptedResponse(accepted=len(batch.items))
