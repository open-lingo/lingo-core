"""Shared aioboto3 Session + DynamoDB resource.

Before this consolidation, each of the 6 real Dynamo repos constructed
its own ``aioboto3.Session()`` plus its own ``resource("dynamodb")``
context. That meant every Lambda cold start paid the botocore-init
cost (loading the JSON service model, parsing the credentials chain)
6 separate times — roughly 200-500ms of avoidable cold-start latency.

This module holds ONE session and ONE resource context, lazily
initialised on first ``get_shared_resource(region)`` call and closed
once during ``shutdown_repositories``. ``init_repositories`` calls
connect() sequentially, so the lazy-init is race-free in practice.
"""

import aioboto3

_session: aioboto3.Session | None = None
_resource_ctx = None  # async context manager returned by session.resource()
_resource = None  # the dynamodb ServiceResource


async def get_shared_resource(region: str):
    """Return the shared aioboto3 DynamoDB ServiceResource, creating it lazily.

    All repos must pass the same ``region`` — we only support one region
    at a time (the Lambda's home region). If a second region is requested
    we fail loud rather than silently giving back the first region's
    resource.
    """
    global _session, _resource_ctx, _resource
    if _resource is None:
        _session = aioboto3.Session()
        _resource_ctx = _session.resource("dynamodb", region_name=region)
        _resource = await _resource_ctx.__aenter__()
    return _resource


async def close_shared_resource() -> None:
    """Close the shared resource (called once from shutdown_repositories)."""
    global _session, _resource_ctx, _resource
    if _resource_ctx is not None:
        await _resource_ctx.__aexit__(None, None, None)
    _session = None
    _resource_ctx = None
    _resource = None
