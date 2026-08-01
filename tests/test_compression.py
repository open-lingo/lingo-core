"""Response compression contract.

``GET /srs/state`` hands back a learner's entire card store on every app load.
Measured at ~447 bytes/card, a 6k-card learner is ~2.56 MiB raw against the
Lambda Function URL's hard 6 MB buffered-response cap — so compression is not a
nicety here, it is headroom. This module locks both halves of that path: that
the middleware compresses at all, and that the compressed bytes survive Mangum's
base64 decision on the way out of Lambda.
"""

import base64
import gzip
import json

from mangum.adapter import DEFAULT_TEXT_MIME_TYPES
from mangum.handlers.utils import handle_base64_response_body


def _modal_state(card: int) -> dict:
    """A realistic FSRS-6 modal card. Values vary per card so the fixture
    compresses like real data rather than like a repeated string."""
    return {
        "recognition": {
            "stability": 1.5 + card * 0.37,
            "difficulty": 5.2 + (card % 7) * 0.11,
            "state": "review",
            "interval": card % 40,
            "dueDate": f"2026-08-{(card % 28) + 1:02d}",
            "lastReviewDate": f"2026-07-{(card % 28) + 1:02d}",
            "reps": card % 30,
            "lapses": card % 4,
        },
        "production": {
            "stability": 0.5 + card * 0.19,
            "difficulty": 6.0 + (card % 5) * 0.23,
            "state": "learning",
            "interval": 0,
            "dueDate": f"2026-08-{(card % 27) + 2:02d}",
            "lastReviewDate": f"2026-07-{(card % 28) + 1:02d}",
            "reps": card % 12,
            "lapses": card % 3,
            "learningSteps": card % 3,
        },
    }


def test_srs_state_response_is_gzipped(api_client) -> None:
    """A card store past the size floor comes back gzip-encoded and intact."""
    client, _user_id, _admin = api_client

    cards = {f"ja:atom-{i:04d}": _modal_state(i) for i in range(40)}
    resp = client.post("/api/core/v1/srs/sync", json={"cards": cards})
    assert resp.status_code == 200, resp.text

    resp = client.get(
        "/api/core/v1/srs/state", headers={"Accept-Encoding": "gzip"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-encoding") == "gzip"
    # httpx transparently decompresses, so the payload must still be whole.
    state = resp.json()["cards"]
    assert len(state) == 40
    assert state["ja:atom-0007"]["recognition"]["reps"] == 7


def test_small_response_is_not_compressed(api_client) -> None:
    """Below ``minimum_size`` gzip is pure overhead — leave it alone."""
    client, _user_id, _admin = api_client
    resp = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


def test_cors_headers_survive_compression(api_client) -> None:
    """CORS is added OUTSIDE gzip; a compressed response must still carry it,
    or every browser call fails CORS the moment a payload crosses the floor."""
    client, _user_id, _admin = api_client
    cards = {f"ja:atom-{i:04d}": _modal_state(i) for i in range(40)}
    client.post("/api/core/v1/srs/sync", json={"cards": cards})

    resp = client.get(
        "/api/core/v1/srs/state",
        headers={"Accept-Encoding": "gzip", "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert "access-control-allow-origin" in resp.headers


def test_mangum_base64_encodes_gzipped_json() -> None:
    """The non-obvious half of the path.

    ``GZipMiddleware`` leaves ``content-type: application/json``, which IS in
    Mangum's text-mime list — so ``handle_base64_response_body`` tries
    ``body.decode()`` BEFORE considering base64, and only base64-encodes when
    that raises. Compressed bytes therefore reach Lambda intact only via an
    exception path, which is worth pinning: gzip's magic number puts 0x8b at
    byte 2, a UTF-8 continuation byte in a lead position, so the decode always
    raises. If a future Mangum ever swallows that error, responses would go out
    as mojibake with no test to catch it.
    """
    payload = json.dumps({"cards": {f"c{i}": _modal_state(i) for i in range(40)}})
    body = gzip.compress(payload.encode())
    assert body[:3] == b"\x1f\x8b\x08"  # the guarantee the fallback rests on
    assert "application/json" in DEFAULT_TEXT_MIME_TYPES

    out_body, is_b64 = handle_base64_response_body(
        body,
        {"content-type": "application/json", "content-encoding": "gzip"},
        DEFAULT_TEXT_MIME_TYPES,
    )

    assert is_b64 is True, "gzipped body must be flagged base64 for Lambda"
    assert json.loads(gzip.decompress(base64.b64decode(out_body))) == json.loads(payload)


def test_uncompressed_json_is_not_base64_encoded() -> None:
    """Control: plain JSON still goes out as text, so the test above is
    detecting gzip specifically and not just asserting Mangum's default."""
    body = json.dumps({"status": "ok"}).encode()
    out_body, is_b64 = handle_base64_response_body(
        body, {"content-type": "application/json"}, DEFAULT_TEXT_MIME_TYPES
    )
    assert is_b64 is False
    assert out_body == '{"status": "ok"}'
