"""DynamoDB-backed leaderboard read repository (production).

Reads the precomputed ``lingo_social_leaderboard`` table that ``lingo-async``
writes. lingo-core is read-only here — the async worker owns all rollup writes
(see the "Leaderboard ownership" note in the backend CLAUDE.md).

Item layout (written by ``lingo-async/app/leaderboard/updater.py``):
  PK = BUCKET#<bucket>   SK = USER#<user_id>   xp (N)   ttl (N)

GSI ``BucketXp-Index`` (``lingo-infra/main.tf``, added 2026-06-17 for this read
path): hash = ``PK`` (reuses the table partition key), range = ``xp`` (Number),
projection KEYS_ONLY. The index needs no extra attributes — the existing
writer already sets ``PK`` + ``xp``, so there is NO writer change required for
the top-N / rank reads to work. The KEYS_ONLY projection returns PK + SK + xp,
which is all ``top_n`` needs (user_id is parsed from SK; xp is the range key).

NOTE (writer gap, display fields only): the table stores xp + ids but NOT
username / display_name / profile_picture_key, so the router hydrates those per
page entry via the user repo (bounded by limit). A follow-up that mirrors
display fields onto the writer item would drop even that bounded fan-out.
"""

from typing import Any

from app.db.dynamo._session import get_shared_resource

_BUCKET_PREFIX = "BUCKET#"
_USER_PREFIX = "USER#"
_GSI = "BucketXp-Index"


def _user_id_from_sk(sk: str) -> str:
    return sk[len(_USER_PREFIX) :] if sk.startswith(_USER_PREFIX) else sk


def _to_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


class DynamoLeaderboardRepository:
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

    def _pk(self, bucket: str) -> str:
        return f"{_BUCKET_PREFIX}{bucket}"

    async def top_n(self, bucket: str, limit: int) -> list[dict[str, Any]]:
        resp = await self._table.query(
            IndexName=_GSI,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": self._pk(bucket)},
            ScanIndexForward=False,  # highest xp first
            Limit=limit,
        )
        return [
            {"user_id": _user_id_from_sk(i["SK"]), "xp": _to_int(i.get("xp"))}
            for i in resp.get("Items", [])
        ]

    async def get_entry(self, bucket: str, user_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(bucket), "SK": f"{_USER_PREFIX}{user_id}"}
        )
        item = resp.get("Item")
        if not item:
            return None
        return {"user_id": user_id, "xp": _to_int(item.get("xp"))}

    async def rank_for_xp(self, bucket: str, xp: int) -> int:
        # 1-based rank = (#rows with strictly greater xp) + 1. Bounded COUNT
        # query on the GSI range — no item payload returned.
        resp = await self._table.query(
            IndexName=_GSI,
            KeyConditionExpression="PK = :pk AND xp > :xp",
            ExpressionAttributeValues={":pk": self._pk(bucket), ":xp": xp},
            Select="COUNT",
        )
        return int(resp.get("Count", 0)) + 1

    async def bucket_size(self, bucket: str) -> int:
        resp = await self._table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": self._pk(bucket)},
            Select="COUNT",
        )
        return int(resp.get("Count", 0))
