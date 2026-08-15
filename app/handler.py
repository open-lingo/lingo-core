"""Lambda entrypoint — wraps the FastAPI app with Mangum."""

from typing import Any

from mangum import Mangum

from app.main import app

_asgi_handler = Mangum(app, lifespan="on")


def handler(event: Any, context: Any) -> Any:
    """Route Lambda events to the ASGI app, except warm pings.

    The EventBridge warmer (lingo-infra `lingo_core_warmer.tf`) invokes this
    function every few minutes with ``{"warmer": true}`` so an initialized
    instance is usually available — a cold start measured ~2.4–2.9 s in prod
    (2026-08-15), which every first-API-call-of-a-session paid. The ping must
    short-circuit BEFORE Mangum: it isn't an HTTP event, and Mangum raises on
    shapes it doesn't recognize. Import cost is the whole point — by the time
    this function body runs, the app module (the expensive part) is loaded.
    """
    if isinstance(event, dict) and event.get("warmer"):
        return {"warmed": True}
    return _asgi_handler(event, context)
