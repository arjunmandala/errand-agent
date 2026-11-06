"""Append-only JSONL trace per run."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from errand_agent.sanitize import redact


class Tracer:
    def __init__(self, trace_dir, session_id: str, clock, run_id: str | None = None) -> None:
        self.clock = clock
        self.run_id = run_id or f"run-{clock.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        d = Path(trace_dir) / session_id
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{self.run_id}.jsonl"
        self.events: list[dict] = []
        self._seq = 0

    def event(self, kind: str, /, **data) -> dict:
        self._seq += 1
        ev = {"seq": self._seq, "ts": self.clock.now().isoformat(), "run_id": self.run_id, **redact(data), "kind": kind}
        self.events.append(ev)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        return ev
