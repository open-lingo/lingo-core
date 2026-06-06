"""DynamoDB-backed social repository (production).

Single-table layout — PK is a stable per-domain string, SK encodes the row kind:

  PK = USER#<id>                  SK = FRIEND#<friend_id>
  PK = USER#<id>                  SK = REQUEST_IN#<from_id>
  PK = USER#<id>                  SK = REQUEST_OUT#<to_id>
  PK = USER#<id>                  SK = BLOCK#<blocked_id>
  PK = USER#<id>                  SK = ACTIVITY#<created_at>#<activity_id>
  PK = ACTIVITY#<activity_id>     SK = META
  PK = ACTIVITY#<activity_id>     SK = REACTION#<activity_id>#<kind>#<user_id>
  PK = INVITE#<code>              SK = META
  PK = INVITE_OWNER#<owner_id>    SK = META
  PK = INVITE#<code>              SK = REDEMPTION#<invitee_id>
  PK = INVITER#<owner_id>         SK = REDEMPTION#<year_month>#<invitee_id>
  PK = THREAD#<thread_id>         SK = META
  PK = THREAD#<thread_id>         SK = MESSAGE#<sent_at>#<message_id>
  PK = USER#<id>                  SK = THREAD#<thread_id>

This module is intentionally light — the SQLite impl is the reference. The
prod tables aren't provisioned yet, so this serves as the contract surface.
The router never touches a concrete repo directly.
"""

from datetime import UTC, datetime
from typing import Any

from app.db.dynamo._session import get_shared_resource


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _year_month(iso: str) -> str:
    return iso[:7]


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    data = dict(item)
    for k in ("PK", "SK", "GSI1PK", "GSI1SK"):
        data.pop(k, None)
    return data


class DynamoSocialRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region
        self._table: Any = None

    async def connect(self) -> None:
        resource = await get_shared_resource(self._region)
        self._table = await resource.Table(self._table_name)

    async def close(self) -> None:
        # Shared resource closed via close_shared_resource(); no-op here.
        pass

    # ── Friends ──────────────────────────────────────────────────────────────

    async def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_id}", ":sk": "FRIEND#"},
        )
        return [_strip_keys(i) for i in resp.get("Items", [])]

    async def is_friend(self, user_id: str, other_id: str) -> bool:
        resp = await self._table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"FRIEND#{other_id}"})
        return resp.get("Item") is not None

    async def add_friend_edge(self, a_id: str, b_id: str) -> None:
        if a_id == b_id:
            return
        now = _now_iso()
        for x, y in ((a_id, b_id), (b_id, a_id)):
            await self._table.put_item(
                Item={
                    "PK": f"USER#{x}",
                    "SK": f"FRIEND#{y}",
                    "user_id": x,
                    "friend_id": y,
                    "friended_at": now,
                }
            )

    async def remove_friend_edge(self, a_id: str, b_id: str) -> None:
        for x, y in ((a_id, b_id), (b_id, a_id)):
            await self._table.delete_item(Key={"PK": f"USER#{x}", "SK": f"FRIEND#{y}"})

    # ── Friend requests ──────────────────────────────────────────────────────

    async def list_friend_requests(self, user_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        in_resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_id}", ":sk": "REQUEST_IN#"},
        )
        out_resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_id}", ":sk": "REQUEST_OUT#"},
        )
        incoming = [_strip_keys(i) for i in in_resp.get("Items", [])]
        outgoing = [_strip_keys(i) for i in out_resp.get("Items", [])]
        return incoming, outgoing

    async def get_friend_request(self, from_id: str, to_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"USER#{to_id}", "SK": f"REQUEST_IN#{from_id}"})
        item = resp.get("Item")
        return _strip_keys(item) if item else None

    async def upsert_friend_request(self, from_id: str, to_id: str) -> dict[str, Any]:
        now = _now_iso()
        item = {"from_id": from_id, "to_id": to_id, "requested_at": now}
        # Two rows — sender sees outgoing, receiver sees incoming.
        await self._table.put_item(Item={"PK": f"USER#{to_id}", "SK": f"REQUEST_IN#{from_id}", **item})
        await self._table.put_item(Item={"PK": f"USER#{from_id}", "SK": f"REQUEST_OUT#{to_id}", **item})
        return item

    async def delete_friend_request(self, from_id: str, to_id: str) -> None:
        await self._table.delete_item(Key={"PK": f"USER#{to_id}", "SK": f"REQUEST_IN#{from_id}"})
        await self._table.delete_item(Key={"PK": f"USER#{from_id}", "SK": f"REQUEST_OUT#{to_id}"})

    # ── Blocks ───────────────────────────────────────────────────────────────

    async def list_blocks(self, user_id: str) -> list[dict[str, Any]]:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_id}", ":sk": "BLOCK#"},
        )
        return [_strip_keys(i) for i in resp.get("Items", [])]

    async def is_blocked(self, blocker_id: str, blocked_id: str) -> bool:
        resp = await self._table.get_item(Key={"PK": f"USER#{blocker_id}", "SK": f"BLOCK#{blocked_id}"})
        return resp.get("Item") is not None

    async def block_user(self, blocker_id: str, blocked_id: str) -> None:
        if blocker_id == blocked_id:
            return
        await self._table.put_item(
            Item={
                "PK": f"USER#{blocker_id}",
                "SK": f"BLOCK#{blocked_id}",
                "blocker_id": blocker_id,
                "blocked_id": blocked_id,
                "blocked_at": _now_iso(),
            }
        )

    async def unblock_user(self, blocker_id: str, blocked_id: str) -> None:
        await self._table.delete_item(Key={"PK": f"USER#{blocker_id}", "SK": f"BLOCK#{blocked_id}"})

    # ── Activity feed ────────────────────────────────────────────────────────

    async def list_activity(
        self,
        user_id: str,
        friend_ids: list[str],
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        # Fan-out: query each user's activity rows, merge, sort, slice.
        items: list[dict[str, Any]] = []
        for uid in [user_id, *friend_ids]:
            resp = await self._table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={":pk": f"USER#{uid}", ":sk": "ACTIVITY#"},
                ScanIndexForward=False,
                Limit=limit + 1,
            )
            items.extend(_strip_keys(i) for i in resp.get("Items", []))
        items.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        if cursor:
            items = [i for i in items if (i.get("created_at") or "") < cursor]
        next_cursor = items[limit - 1]["created_at"] if len(items) > limit else None
        return items[:limit], next_cursor

    async def get_activity(self, activity_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"ACTIVITY#{activity_id}", "SK": "META"})
        item = resp.get("Item")
        return _strip_keys(item) if item else None

    async def put_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        created_at = activity.get("created_at") or _now_iso()
        meta = {
            "id": activity["id"],
            "user_id": activity["user_id"],
            "kind": activity["kind"],
            "payload": activity.get("payload") or {},
            "created_at": created_at,
        }
        # ACTIVITY#<id> META row for direct lookup.
        await self._table.put_item(Item={"PK": f"ACTIVITY#{activity['id']}", "SK": "META", **meta})
        # USER#<id> ACTIVITY#<ts>#<id> row for the per-user feed query.
        sk = f"ACTIVITY#{created_at}#{activity['id']}"
        await self._table.put_item(Item={"PK": f"USER#{activity['user_id']}", "SK": sk, **meta})
        return meta

    async def list_reactions(self, activity_id: str) -> list[dict[str, Any]]:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"ACTIVITY#{activity_id}",
                ":sk": f"REACTION#{activity_id}#",
            },
        )
        return [_strip_keys(i) for i in resp.get("Items", [])]

    async def list_reactions_bulk(self, activity_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {aid: [] for aid in activity_ids}
        for aid in activity_ids:
            out[aid] = await self.list_reactions(aid)
        return out

    async def toggle_reaction(self, activity_id: str, user_id: str, kind: str) -> tuple[bool, int]:
        sk = f"REACTION#{activity_id}#{kind}#{user_id}"
        key = {"PK": f"ACTIVITY#{activity_id}", "SK": sk}
        existing = await self._table.get_item(Key=key)
        if existing.get("Item"):
            await self._table.delete_item(Key=key)
            mine_after = False
        else:
            await self._table.put_item(
                Item={
                    **key,
                    "activity_id": activity_id,
                    "user_id": user_id,
                    "kind": kind,
                    "created_at": _now_iso(),
                }
            )
            mine_after = True
        # Count after toggle — scan kind-prefix.
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"ACTIVITY#{activity_id}",
                ":sk": f"REACTION#{activity_id}#{kind}#",
            },
            Select="COUNT",
        )
        count_after = int(resp.get("Count", 0))
        return mine_after, count_after

    # ── Invite codes / redemptions ───────────────────────────────────────────

    async def get_invite_code_for_owner(self, owner_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"INVITE_OWNER#{owner_id}", "SK": "META"})
        item = resp.get("Item")
        return _strip_keys(item) if item else None

    async def create_invite_code(self, owner_id: str, code: str) -> dict[str, Any]:
        existing = await self.get_invite_code_for_owner(owner_id)
        if existing:
            return existing
        now = _now_iso()
        row = {"code": code, "owner_id": owner_id, "created_at": now}
        await self._table.put_item(Item={"PK": f"INVITE_OWNER#{owner_id}", "SK": "META", **row})
        await self._table.put_item(Item={"PK": f"INVITE#{code}", "SK": "META", **row})
        return row

    async def get_invite_code(self, code: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"INVITE#{code}", "SK": "META"})
        item = resp.get("Item")
        return _strip_keys(item) if item else None

    async def count_redemptions_for_owner_in_month(self, owner_id: str, year_month: str) -> int:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"INVITER#{owner_id}",
                ":sk": f"REDEMPTION#{year_month}#",
            },
            Select="COUNT",
        )
        return int(resp.get("Count", 0))

    async def get_redemption(self, code: str, invitee_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"INVITE#{code}", "SK": f"REDEMPTION#{invitee_id}"})
        item = resp.get("Item")
        return _strip_keys(item) if item else None

    async def upsert_redemption(self, redemption: dict[str, Any]) -> dict[str, Any]:
        ts = redemption.get("redeemed_at") or _now_iso()
        ym = redemption.get("year_month") or _year_month(ts)
        row = {**redemption, "redeemed_at": ts, "year_month": ym}
        await self._table.put_item(
            Item={
                "PK": f"INVITE#{redemption['code']}",
                "SK": f"REDEMPTION#{redemption['invitee_id']}",
                **row,
            }
        )
        # Mirror per-inviter so the monthly cap query is bounded.
        await self._table.put_item(
            Item={
                "PK": f"INVITER#{redemption['inviter_id']}",
                "SK": f"REDEMPTION#{ym}#{redemption['invitee_id']}",
                **row,
            }
        )
        return row

    # ── Threads / messages ───────────────────────────────────────────────────

    async def list_threads_for_user(self, user_id: str) -> list[dict[str, Any]]:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_id}", ":sk": "THREAD#"},
        )
        return [_strip_keys(i) for i in resp.get("Items", [])]

    async def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"THREAD#{thread_id}", "SK": "META"})
        item = resp.get("Item")
        return _strip_keys(item) if item else None

    async def put_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        row = {
            "id": thread["id"],
            "user_a_id": thread["user_a_id"],
            "user_b_id": thread["user_b_id"],
            "created_at": thread.get("created_at") or now,
            "updated_at": thread.get("updated_at") or now,
        }
        await self._table.put_item(Item={"PK": f"THREAD#{thread['id']}", "SK": "META", **row})
        for uid in (thread["user_a_id"], thread["user_b_id"]):
            await self._table.put_item(Item={"PK": f"USER#{uid}", "SK": f"THREAD#{thread['id']}", **row})
        return row

    async def list_messages(self, thread_id: str) -> list[dict[str, Any]]:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": f"THREAD#{thread_id}", ":sk": "MESSAGE#"},
        )
        return [_strip_keys(i) for i in resp.get("Items", [])]

    async def put_message(self, message: dict[str, Any]) -> dict[str, Any]:
        ts = message.get("sent_at") or _now_iso()
        sk = f"MESSAGE#{ts}#{message['id']}"
        row = {
            "id": message["id"],
            "thread_id": message["thread_id"],
            "sender_id": message["sender_id"],
            "body": message["body"],
            "sent_at": ts,
        }
        await self._table.put_item(Item={"PK": f"THREAD#{message['thread_id']}", "SK": sk, **row})
        return row
