"""SQLite persistence for workflows, messages, approvals, operations, and bookings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _loads(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


class Store:
    """One SQLite connection per store. Writes autocommit."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying SQLite connection."""
        return self._conn

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                digest TEXT,
                status TEXT,
                created_at TEXT,
                expires_at TEXT,
                reason TEXT
            );
            CREATE TABLE IF NOT EXISTS operations (
                idempotency_key TEXT PRIMARY KEY,
                approval_id TEXT,
                kind TEXT,
                request TEXT,
                status TEXT,
                result TEXT,
                attempts INTEGER,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT,
                value TEXT,
                kind TEXT,
                confirmed INTEGER,
                scope TEXT,
                status TEXT,
                source TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def save_workflow(self, sid: str, data: dict) -> None:
        """Insert or replace the serialized workflow for a session."""
        self._conn.execute(
            "INSERT INTO workflows (session_id, data) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data",
            (sid, json.dumps(data)),
        )

    def load_workflow(self, sid: str) -> dict | None:
        """Return the workflow dict, or None if the session is unknown."""
        row = self._conn.execute(
            "SELECT data FROM workflows WHERE session_id = ?", (sid,)
        ).fetchone()
        if row is None:
            return None
        return _loads(row["data"])

    def add_message(
        self, sid: str, role: str, content: str, ts: str | None = None
    ) -> dict:
        """Append a chat message and return the stored row."""
        stamp = ts if ts is not None else self._now()
        cur = self._conn.execute(
            "INSERT INTO messages (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (sid, role, content, stamp),
        )
        return {
            "id": cur.lastrowid,
            "session_id": sid,
            "role": role,
            "content": content,
            "ts": stamp,
        }

    def recent_messages(self, sid: str, limit: int | None = None) -> list[dict]:
        """Messages for a session, oldest-first. ``limit`` keeps the newest N."""
        if limit is None:
            rows = self._conn.execute(
                "SELECT id, session_id, role, content, ts FROM messages "
                "WHERE session_id = ? ORDER BY id ASC",
                (sid,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, session_id, role, content, ts FROM ("
                "  SELECT id, session_id, role, content, ts FROM messages "
                "  WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (sid, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_approval(self, row: dict) -> dict:
        """Insert an approval row. ``details`` is stored as JSON."""
        details = row.get("details")
        stored = {
            "id": row["id"],
            "session_id": row["session_id"],
            "action": row["action"],
            "details": json.dumps(details if details is not None else {}),
            "digest": row.get("digest"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "expires_at": row.get("expires_at"),
            "reason": row.get("reason"),
        }
        self._conn.execute(
            "INSERT INTO approvals (id, session_id, action, details, digest, status, "
            "created_at, expires_at, reason) VALUES "
            "(:id, :session_id, :action, :details, :digest, :status, :created_at, "
            ":expires_at, :reason)",
            stored,
        )
        return self.get_approval(row["id"])  # type: ignore[return-value]

    def get_approval(self, approval_id: str) -> dict | None:
        """Return one approval with ``details`` parsed, or None."""
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        return self._approval_dict(row)

    def update_approval(self, approval_id: str, **cols: Any) -> dict | None:
        """Update named approval columns. Dict values are JSON-encoded."""
        if not cols:
            return self.get_approval(approval_id)
        allowed = {
            "session_id",
            "action",
            "details",
            "digest",
            "status",
            "created_at",
            "expires_at",
            "reason",
        }
        sets: list[str] = []
        values: list[Any] = []
        for key, value in cols.items():
            if key not in allowed:
                continue
            if key == "details" and not isinstance(value, str):
                value = json.dumps(value if value is not None else {})
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_approval(approval_id)
        values.append(approval_id)
        self._conn.execute(
            f"UPDATE approvals SET {', '.join(sets)} WHERE id = ?", values
        )
        return self.get_approval(approval_id)

    def pending_approval(self, sid: str) -> dict | None:
        """Latest approval for the session whose status is pending."""
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE session_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if row is None:
            return None
        return self._approval_dict(row)

    def approvals_for(self, sid: str) -> list[dict]:
        """All approvals for a session, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM approvals WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (sid,),
        ).fetchall()
        return [self._approval_dict(row) for row in rows]

    def begin_operation(
        self, key: str, approval_id: str, kind: str, request: dict
    ) -> dict:
        """Write-ahead insert. Existing key is left unchanged (INSERT OR IGNORE)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO operations "
            "(idempotency_key, approval_id, kind, request, status, result, attempts, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', NULL, 0, ?)",
            (key, approval_id, kind, json.dumps(request or {}), self._now()),
        )
        return self.get_operation(key)  # type: ignore[return-value]

    def get_operation(self, key: str) -> dict | None:
        """Return one operation with JSON columns parsed, or None."""
        row = self._conn.execute(
            "SELECT * FROM operations WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return self._operation_dict(row)

    def finish_operation(self, key: str, status: str, result: Any = None) -> dict | None:
        """Set terminal status and optional result JSON."""
        self._conn.execute(
            "UPDATE operations SET status = ?, result = ?, updated_at = ? "
            "WHERE idempotency_key = ?",
            (status, _dumps(result) if result is not None else None, self._now(), key),
        )
        return self.get_operation(key)

    def bump_attempts(self, key: str) -> dict | None:
        """Increment the attempt counter for an operation."""
        self._conn.execute(
            "UPDATE operations SET attempts = COALESCE(attempts, 0) + 1, updated_at = ? "
            "WHERE idempotency_key = ?",
            (self._now(), key),
        )
        return self.get_operation(key)

    def operations_with_status(self, status: str) -> list[dict]:
        """Operations currently in ``status``, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM operations WHERE status = ? ORDER BY rowid ASC", (status,)
        ).fetchall()
        return [self._operation_dict(row) for row in rows]

    def upsert_booking(self, booking: dict) -> dict:
        """Insert or replace a booking ledger row keyed by ``booking_id``."""
        data = dict(booking)
        booking_id = data["booking_id"]
        self._conn.execute(
            "INSERT INTO bookings (booking_id, data) VALUES (?, ?) "
            "ON CONFLICT(booking_id) DO UPDATE SET data = excluded.data",
            (booking_id, json.dumps(data)),
        )
        return data

    def bookings(self) -> list[dict]:
        """All ledger bookings, in insertion order."""
        rows = self._conn.execute(
            "SELECT data FROM bookings ORDER BY rowid ASC"
        ).fetchall()
        return [_loads(row["data"]) for row in rows]

    def _approval_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["details"] = _loads(data.get("details")) or {}
        return data

    def _operation_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["request"] = _loads(data.get("request"))
        data["result"] = _loads(data.get("result"))
        return data
