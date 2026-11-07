"""Explicit workflow state machine for an errand session."""

from __future__ import annotations

import re
from enum import Enum


def normalize_time(value):
    """'9', '9:00', '9am' or '10 a.m.' become 'HH:MM' so string comparison with slot times is safe.

    Anything unparseable is returned unchanged.
    """
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", str(value).strip().lower().replace(".", ""))
    if not m:
        return value
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3] == "pm" and hour < 12:
        hour += 12
    elif m[3] == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return value
    return f"{hour:02d}:{minute:02d}"


class State(str, Enum):
    """Lifecycle of a single errand request."""

    COLLECTING = "collecting"
    SEARCHING = "searching"
    PROPOSING = "proposing"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    DONE = "done"
    FAILED = "failed"


# Whitelist of ordinary transitions. Any state may also move to COLLECTING or FAILED.
# A self-transition is a no-op and is allowed only when listed here.
TRANSITIONS: dict[State, set[State]] = {
    State.COLLECTING: {State.SEARCHING, State.AWAITING_APPROVAL},
    State.SEARCHING: {State.PROPOSING},
    State.PROPOSING: {State.AWAITING_APPROVAL, State.SEARCHING},
    State.AWAITING_APPROVAL: {
        State.EXECUTING,
        State.PROPOSING,
        State.SEARCHING,
        State.AWAITING_APPROVAL,
    },
    State.EXECUTING: {
        State.VERIFYING,
        State.DONE,
        State.AWAITING_APPROVAL,
        State.RECOVERING,
    },
    State.VERIFYING: {State.DONE, State.RECOVERING},
    State.RECOVERING: {State.DONE, State.AWAITING_APPROVAL},
    State.DONE: set(),
    State.FAILED: set(),
}


class IllegalTransition(Exception):
    """Raised when a workflow move is not on the whitelist."""


class Workflow:
    """Mutable errand request plus its allowed state transitions."""

    REQUEST_FIELDS = (
        "service",
        "area",
        "date_range",
        "time_window",
        "earliest_time",
        "budget_max",
        "preferences",
        "provider_id",
    )
    REQUIRED = ["service", "area", "date_range", "time_window", "budget_max"]

    def __init__(self) -> None:
        self.state = State.COLLECTING
        self.request: dict = {}
        self.history: list[tuple[str, str]] = []
        self.candidates: list = []
        self.active_approval_id: str | None = None
        self.last_outcome: dict | None = None

    def update(self, **fields: object) -> None:
        """Set known request fields. Unknown keys and None values are ignored."""
        for name, value in fields.items():
            if name in self.REQUEST_FIELDS and value is not None:
                self.request[name] = normalize_time(value) if name == "earliest_time" else value

    def missing(self) -> list[str]:
        """Required field names that have not been set, in REQUIRED order."""
        return [name for name in self.REQUIRED if self.request.get(name) is None]

    def transition(self, to: State | str) -> State:
        """Move to ``to`` if the whitelist (plus reset/fail) allows it.

        A listed self-transition is a no-op: state and history are unchanged.
        """
        target = to if isinstance(to, State) else State(to)
        if target == self.state:
            if target not in TRANSITIONS[self.state]:
                raise IllegalTransition(
                    f"self-transition {self.state.value} -> {target.value} is not allowed"
                )
            return self.state
        allowed = set(TRANSITIONS[self.state])
        allowed.update({State.COLLECTING, State.FAILED})
        if target not in allowed:
            raise IllegalTransition(
                f"illegal transition {self.state.value} -> {target.value}"
            )
        previous = self.state
        self.state = target
        self.history.append((previous.value, target.value))
        return self.state

    def to_dict(self) -> dict:
        """Serialize the workflow for persistence."""
        return {
            "state": self.state.value,
            "request": dict(self.request),
            "history": [list(item) for item in self.history],
            "candidates": list(self.candidates),
            "active_approval_id": self.active_approval_id,
            "last_outcome": self.last_outcome,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Workflow:
        """Restore a workflow previously produced by ``to_dict``."""
        wf = cls()
        wf.state = State(data["state"])
        wf.request = dict(data.get("request") or {})
        wf.history = [tuple(item) for item in data.get("history") or []]
        wf.candidates = list(data.get("candidates") or [])
        wf.active_approval_id = data.get("active_approval_id")
        wf.last_outcome = data.get("last_outcome")
        return wf
