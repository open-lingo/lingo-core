"""Per-request id, sourced from the Lambda invocation when available.

Mangum copies the raw Lambda event/context onto the ASGI scope as
`scope["aws.event"]` / `scope["aws.context"]` (see `mangum.adapter.Mangum`
and `app/handler.py`), so in prod every request already carries a stable
`context.aws_request_id` — the same id CloudWatch stamps on every log line
Lambda itself writes (`RequestId: ...` in the platform lines). Echoing it
back as `X-Request-Id` lets a client pair its own error report's
`lastRequestId` with the exact CloudWatch invocation, no correlation table
needed.

Outside Lambda (local uvicorn, pytest's `TestClient`, which never runs
through Mangum) there is no `aws.context`, so this falls back to a fresh
uuid4 per request — still unique, just not traceable to a Lambda log
stream, which is expected for local dev.
"""

import uuid

from starlette.requests import Request


def get_request_id(request: Request) -> str:
    """Return this request's id, computing and caching it on first call.

    Cached on `request.state` so multiple call sites (an exception handler
    AND a router, say) agree on one id per request instead of minting two.
    """
    cached = getattr(request.state, "request_id", None)
    if isinstance(cached, str) and cached:
        return cached

    request_id: str | None = None
    context = request.scope.get("aws.context")
    if context is not None:
        request_id = getattr(context, "aws_request_id", None)

    if not request_id:
        request_id = str(uuid.uuid4())

    request.state.request_id = request_id
    return request_id
