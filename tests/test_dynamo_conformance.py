"""Dynamo backend conformance gate (iteration item C3).

Two guards prove the whole stack really runs on DynamoDB in prod:

1. ``test_every_domain_has_real_dynamo_impl`` — a static AST audit asserting
   each domain's ``Dynamo*Repository`` defines every method on its Protocol
   AND that no method body is a silent stub (``pass`` / ``return None`` /
   ``return []`` / ``raise NotImplementedError``). This catches the
   "wired to a real class but the method no-ops" failure mode that the
   runtime wiring assertion can't see.

2. ``test_provider_wiring_assertion_under_dynamodb`` — boots
   ``init_repositories()`` under ``DB_BACKEND=dynamodb`` (connects fail with
   no AWS creds, so every domain lands in ``_degraded``) and asserts the
   provider's ``_assert_backend_wiring`` guard does NOT raise — i.e. every
   required domain is wired to a ``Dynamo*Repository``, none silently unwired.

If a future change stubs a Dynamo method or points a domain at a Mock /
SQLite class, one of these fails.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

# domain -> (protocol module, protocol class, dynamo module, dynamo class)
_DOMAINS: dict[str, tuple[str, str, str, str]] = {
    "user": ("user", "UserRepository", "user", "DynamoUserRepository"),
    "srs": ("srs", "SRSRepository", "srs", "DynamoSRSRepository"),
    "deck": ("deck", "DeckRepository", "deck", "DynamoDeckRepository"),
    "subscription": ("subscription", "SubscriptionRepository", "subscription", "DynamoSubscriptionRepository"),
    "story": ("story", "StoryRepository", "story", "DynamoStoryRepository"),
    "progress": ("progress", "ProgressRepository", "progress", "DynamoProgressRepository"),
    "social": ("social", "SocialRepository", "social", "DynamoSocialRepository"),
    "leaderboard": ("leaderboard", "LeaderboardRepository", "leaderboard", "DynamoLeaderboardRepository"),
    "quests": ("quests", "QuestRepository", "quests", "DynamoQuestRepository"),
    "platform_settings": ("platform_settings", "PlatformSettingsRepository", "platform_settings", "DynamoPlatformSettingsRepository"),
    "tag": ("tag", "TagRepository", "tag", "DynamoTagRepository"),
    "audit": ("audit", "AuditRepository", "audit", "DynamoAuditRepository"),
    "community": ("community", "CommunityRepository", "community", "DynamoCommunityRepository"),
}

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _class_methods(path: Path, clsname: str) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == clsname:
            return {
                item.name: item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"class {clsname} not found in {path}")


def _protocol_methods(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("__"):
                    names.add(item.name)
    return names


def _is_silent_stub(fn: ast.AST) -> str | None:
    """Return a reason string if the function body is a trivial stub.

    Ignores a leading docstring. ``connect`` / ``close`` and private
    helpers are exempt — their no-op bodies are legitimate.
    """
    body = list(getattr(fn, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if not body:
        return "empty"
    if len(body) != 1:
        return None
    s = body[0]
    if isinstance(s, ast.Pass):
        return "pass"
    if isinstance(s, ast.Raise):
        exc = s.exc
        name = ""
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            name = exc.func.id
        elif isinstance(exc, ast.Name):
            name = exc.id
        if name == "NotImplementedError":
            return "raise NotImplementedError"
        return None
    if isinstance(s, ast.Return):
        v = s.value
        if v is None:
            return "return-bare"
        if isinstance(v, ast.Constant) and v.value is None:
            return "return-None"
        if isinstance(v, ast.List) and not v.elts:
            return "return-[]"
        if isinstance(v, ast.Dict) and not v.keys:
            return "return-{}"
    return None


# Methods whose no-op / trivial body is a legitimate void return, not a stub.
# update_attempt_steps used to be a stub; it now persists via UpdateItem.
_VOID_OK: set[str] = {"connect", "close"}


@pytest.mark.parametrize("domain", list(_DOMAINS))
def test_every_domain_has_real_dynamo_impl(domain: str) -> None:
    proto_mod, _proto_cls, dyn_mod, dyn_cls = _DOMAINS[domain]
    proto_path = _REPO_ROOT / "app" / "db" / "protocols" / f"{proto_mod}.py"
    dyn_path = _REPO_ROOT / "app" / "db" / "dynamo" / f"{dyn_mod}.py"

    proto_methods = _protocol_methods(proto_path)
    impl_methods = _class_methods(dyn_path, dyn_cls)

    missing = sorted(m for m in proto_methods if m not in impl_methods)
    assert not missing, f"{dyn_cls} is missing protocol methods: {missing}"

    stubs: list[str] = []
    for name, fn in impl_methods.items():
        if name.startswith("_") or name in _VOID_OK:
            continue
        reason = _is_silent_stub(fn)
        if reason:
            stubs.append(f"{name} ({reason})")
    assert not stubs, f"{dyn_cls} has silent stub methods: {stubs}"


@pytest.mark.asyncio
async def test_provider_wiring_assertion_under_dynamodb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider must wire every domain to a Dynamo* repo under dynamodb.

    Connects fail (no AWS creds in CI) so every domain degrades — that's
    fine. The wiring assertion runs regardless and proves no domain is
    silently unwired or pointed at a non-Dynamo class.
    """
    monkeypatch.setenv("DB_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_TABLE_PREFIX", "lingo_")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    # Keep boto3 from reaching the network / hanging on credential lookup.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")

    from app import config as config_mod

    importlib.reload(config_mod)

    from app.db import provider as provider_mod

    importlib.reload(provider_mod)
    assert provider_mod.settings.DB_BACKEND == "dynamodb"

    # Must not raise — _assert_backend_wiring runs at the end of init.
    await provider_mod.init_repositories()

    # Every required domain is accounted for: either a live Dynamo repo or
    # recorded as degraded (connect failed). Nothing silently None.
    degraded = provider_mod.degraded_domains()
    g = vars(provider_mod)
    for domain, attr in provider_mod._REQUIRED_DOMAINS.items():
        repo = g.get(attr)
        if repo is None:
            assert domain in degraded, f"{domain} is None but not degraded"
        else:
            assert type(repo).__name__.startswith("Dynamo"), (
                f"{domain} resolved to {type(repo).__name__}, expected Dynamo*"
            )

    await provider_mod.shutdown_repositories()
