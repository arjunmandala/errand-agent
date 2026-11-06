"""Application-enforced approvals. The model never grants these."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta

from errand_agent.store import Store

MATERIAL_FIELDS: dict[str, list[str]] = {
    "book": ["provider_id", "slot", "price"],
    "cancel": ["booking_id", "provider_id", "slot"],
    "reschedule": ["booking_id", "provider_id", "slot", "old_slot"],
}

_CONFIRM = {
    "yes",
    "yeah",
    "yep",
    "yup",
    "confirm",
    "confirmed",
    "ok",
    "okay",
    "sure",
    "proceed",
    "approve",
    "approved",
}
_FILLER = {
    "go",
    "ahead",
    "book",
    "it",
    "do",
    "please",
    "that",
    "thats",
    "fine",
    "sounds",
    "good",
    "lets",
    "the",
    "pls",
}
_REJECT = {
    "no",
    "nope",
    "nah",
    "dont",
    "stop",
    "reject",
    "cancel",
    "not",
    "never",
}
_EMOTICON = re.compile(r"[:;][-']?[)dDpP(/\\|]+")


class ApprovalError(Exception):
    """Raised when an approval cannot be granted or consumed."""


def canonical_digest(action: str, details: dict | None) -> str:
    """SHA-256 of the action plus its material fields only.

    ``hold_id``, service, and other non-material keys do not affect the digest.
    """
    details = details or {}
    payload: dict = {"action": action}
    for key in MATERIAL_FIELDS.get(action, []):
        payload[key] = details.get(key)
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_decision(text: str | None) -> str:
    """Classify a user reply as confirm, reject, or other.

    Deterministic token rules only — never a model. A confirm requires at least
    one confirm word, no reject words, and every token in the confirm/filler
    set. The idiom "go ahead" counts as a confirm signal. Reject is symmetric.
    Anything else, including mixed or extra words, is other.
    """
    if not text:
        return "other"
    lowered = text.lower()
    lowered = _EMOTICON.sub(" ", lowered)
    lowered = lowered.replace("'", "").replace("’", "").replace("`", "")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [tok for tok in lowered.split() if tok]
    if not tokens:
        return "other"
    confirm_hits = sum(1 for tok in tokens if tok in _CONFIRM)
    reject_hits = sum(1 for tok in tokens if tok in _REJECT)
    has_go_ahead = any(
        tokens[i] == "go" and tokens[i + 1] == "ahead" for i in range(len(tokens) - 1)
    )
    if has_go_ahead and confirm_hits == 0 and reject_hits == 0:
        confirm_hits = 1
    if (
        confirm_hits >= 1
        and reject_hits == 0
        and all(tok in _CONFIRM or tok in _FILLER for tok in tokens)
    ):
        return "confirm"
    if (
        reject_hits >= 1
        and confirm_hits == 0
        and all(tok in _REJECT or tok in _FILLER for tok in tokens)
    ):
        return "reject"
    return "other"


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class Approvals:
    """Issue, grant, check, and retire approval records."""

    def __init__(self, store: Store, clock) -> None:
        self.store = store
        self.clock = clock

    def request(
        self, session_id: str, action: str, details: dict, ttl_s: int = 900
    ) -> dict:
        """Open a pending approval, superseding any pending one for the session."""
        pending = self.store.pending_approval(session_id)
        while pending is not None:
            self.invalidate(pending["id"], "superseded")
            pending = self.store.pending_approval(session_id)
        created = self.clock.now()
        expires = created + timedelta(seconds=ttl_s)
        frozen = dict(details or {})
        row = {
            "id": "AP-" + uuid.uuid4().hex[:10],
            "session_id": session_id,
            "action": action,
            "details": frozen,
            "digest": canonical_digest(action, frozen),
            "status": "pending",
            "created_at": created.isoformat(),
            "expires_at": expires.isoformat(),
            "reason": None,
        }
        return self.store.create_approval(row)

    def grant(self, approval_id: str, user_text: str) -> dict:
        """Grant a pending, unexpired approval only on an unambiguous confirm."""
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"unknown approval {approval_id}")
        if approval["status"] != "pending":
            raise ApprovalError(f"approval {approval_id} is {approval['status']}")
        expires = _as_datetime(approval.get("expires_at"))
        if expires is not None and self.clock.now() > expires:
            self.store.update_approval(approval_id, status="expired")
            raise ApprovalError(f"approval {approval_id} has expired")
        if parse_decision(user_text) != "confirm":
            raise ApprovalError("user reply is not an unambiguous confirmation")
        updated = self.store.update_approval(approval_id, status="granted")
        if updated is None:
            raise ApprovalError(f"unknown approval {approval_id}")
        return updated

    def check(self, approval_id: str, action: str, details: dict | None) -> bool:
        """True only for a granted, unexpired approval whose digest matches."""
        approval = self.store.get_approval(approval_id)
        if approval is None:
            return False
        if approval.get("status") != "granted":
            return False
        if approval.get("action") != action:
            return False
        if approval.get("digest") != canonical_digest(action, details or {}):
            return False
        expires = _as_datetime(approval.get("expires_at"))
        if expires is not None and self.clock.now() > expires:
            return False
        return True

    def consume(self, approval_id: str) -> dict | None:
        """Mark a granted approval consumed so it cannot authorize again."""
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"unknown approval {approval_id}")
        if approval["status"] != "granted":
            raise ApprovalError(f"approval {approval_id} is {approval['status']}")
        return self.store.update_approval(approval_id, status="consumed")

    def invalidate(self, approval_id: str, reason: str) -> dict | None:
        """Retire an approval because the action is no longer the one approved."""
        return self.store.update_approval(
            approval_id, status="invalidated", reason=reason
        )

    def reject(self, approval_id: str) -> dict | None:
        """Mark an approval rejected by the user."""
        return self.store.update_approval(approval_id, status="rejected", reason="rejected")
