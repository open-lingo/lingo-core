"""SQLite-backed admin audit log."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS admin_audit (
    id           TEXT PRIMARY KEY,
    actor_id     TEXT NOT NULL,
    action       TEXT NOT NULL,
    target_id    TEXT,
    target_kind  TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_at ON admin_audit (at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_actor ON admin_audit (actor_id, at DESC);
"""


class SqliteAuditRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        d = dict(row)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        return d

    async def append(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        target_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "actor_id": actor_id or "",
            "action": action,
            "target_id": target_id,
            "target_kind": target_kind,
            "payload": json.dumps(payload or {}, default=str),
            "at": datetime.now(UTC).isoformat(),
        }
        await self._conn().execute(
            """INSERT INTO admin_audit (id, actor_id, action, target_id, target_kind, payload, at)
               VALUES (:id, :actor_id, :action, :target_id, :target_kind, :payload, :at)""",
            row,
        )
        await self._conn().commit()
        # Materialize so the response returns a real dict (not the JSON string).
        return {**row, "payload": json.loads(row["payload"])}

    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        actor_id: str | None = None,
        target_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        offset = int(cursor) if cursor else 0
        clauses: list[str] = []
        params: list[Any] = []
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if target_kind:
            clauses.append("target_kind = ?")
            params.append(target_kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        sql = (
            f"SELECT * FROM admin_audit {where} ORDER BY at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit + 1, offset])
        cur = await self._conn().execute(sql, params)
        rows = await cur.fetchall()
        items = [self._row_to_dict(r) for r in rows[:limit]]
        next_cursor = str(offset + limit) if len(rows) > limit else None
        return items, next_cursor
