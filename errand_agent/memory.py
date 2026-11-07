"""Persistent user preferences: explicit vs inferred, correct (supersede) and forget."""
from __future__ import annotations

from errand_agent.workflow import normalize_time

ACTIVE = "active"


class Memory:
    def __init__(self, store, clock) -> None:
        self.store = store
        self.clock = clock

    @property
    def _db(self):
        return self.store.conn

    def _row(self, r) -> dict:
        d = dict(r)
        d["confirmed"] = bool(d["confirmed"])
        return d

    def remember(self, key: str, value, kind: str = "explicit", scope: str = "global",
                 source: str = "user", confirmed: bool | None = None) -> dict:
        if kind not in ("explicit", "inferred"):
            raise ValueError(f"bad kind {kind}")
        confirmed = True if kind == "explicit" else bool(confirmed)
        if key == "earliest_time":
            value = normalize_time(value)
        now = self.clock.now().isoformat()
        self._db.execute(
            "UPDATE memories SET status='superseded', updated_at=? WHERE key=? AND scope=? AND status='active'",
            (now, key, scope))
        cur = self._db.execute(
            "INSERT INTO memories(key,value,kind,confirmed,scope,status,source,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (key, str(value), kind, int(confirmed), scope, ACTIVE, source, now, now))
        self._db.commit()
        return self._get(cur.lastrowid)

    def _get(self, mid: int) -> dict:
        return self._row(self._db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone())

    def correct(self, key: str, value, scope: str = "global") -> dict:
        return self.remember(key, value, kind="explicit", scope=scope, source="user")

    def forget(self, key: str, scope: str | None = None) -> int:
        now = self.clock.now().isoformat()
        q, args = "UPDATE memories SET status='forgotten', updated_at=? WHERE key=? AND status='active'", [now, key]
        if scope:
            q += " AND scope=?"
            args.append(scope)
        n = self._db.execute(q, args).rowcount
        self._db.commit()
        return n

    def confirm(self, key: str) -> int:
        n = self._db.execute(
            "UPDATE memories SET confirmed=1, updated_at=? WHERE key=? AND status='active' AND kind='inferred'",
            (self.clock.now().isoformat(), key)).rowcount
        self._db.commit()
        return n

    def list(self, include_inactive: bool = False) -> list[dict]:
        q = "SELECT * FROM memories" + ("" if include_inactive else " WHERE status='active'") + " ORDER BY id"
        return [self._row(r) for r in self._db.execute(q).fetchall()]

    def eligible(self, service: str | None = None) -> list[dict]:
        """Only facts allowed to reach the model: active, and explicit or user-confirmed."""
        scopes = {"global", service} if service else {"global"}
        return [m for m in self.list() if m["scope"] in scopes and (m["kind"] == "explicit" or m["confirmed"])]

    def get(self, key: str, service: str | None = None):
        rows = [m for m in self.eligible(service) if m["key"] == key]
        rows.sort(key=lambda m: m["scope"] == "global")
        return rows[0]["value"] if rows else None
