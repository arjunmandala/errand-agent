"""Shared harness for offline evals: scripted model + simulated user + invariant checks."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from errand_agent.agent import Agent, Limits
from errand_agent.approval import canonical_digest
from errand_agent.clock import FakeClock
from errand_agent.mocks.env import MockToolEnv, ScenarioConfig
from errand_agent.models import ModelResponse, ScriptedModel, ToolCall
from errand_agent.store import Store

START = datetime(2026, 11, 10, 8, 0)  # Tue
WEEK = ["2026-11-10", "2026-11-14"]


def call(name, **args):
    return ModelResponse(tool_calls=[ToolCall(id=f"c-{name}", name=name, args=args)],
                         usage={"input_tokens": 0, "output_tokens": 0})


def say(text):
    return ModelResponse(text=text, usage={"input_tokens": 0, "output_tokens": 0})


ACTIVE: list = []  # every harness created during a scenario; the runner checks invariants on all of them


class Harness:
    def __init__(self, workdir: Path, script=None, limits=None, budget=4000, seed=7, env=None, db="agent.db", **cfg):
        ACTIVE.append(self)
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.clock = env.clock if env else FakeClock(START)
        self.env = env or MockToolEnv(seed=seed, clock=self.clock, config=ScenarioConfig(**cfg))
        self.model = ScriptedModel(script or [])
        self.store = Store(self.workdir / db)
        self.agent = Agent(env=self.env, model=self.model, store=self.store, clock=self.clock, session_id="eval",
                           limits=limits or Limits(), trace_dir=self.workdir / "traces", context_budget=budget)
        self.transcript: list[tuple[str, str]] = []

    def user(self, text):
        r = self.agent.handle(text)
        self.transcript += [("user", text), (r.kind, r.text)]
        return r

    def restart(self, script=None):
        """Simulate a process restart: new Store/Agent over the same DB, same external provider service."""
        h = Harness.__new__(Harness)
        ACTIVE.append(h)
        h.workdir, h.clock, h.env = self.workdir, self.clock, self.env
        h.model = ScriptedModel(script or [])
        h.store = Store(self.workdir / "agent.db")
        h.agent = Agent(env=self.env, model=h.model, store=h.store, clock=self.clock, session_id="eval",
                        limits=self.agent.limits, trace_dir=self.workdir / "traces",
                        context_budget=self.agent.context.budget)
        h.transcript = self.transcript
        return h

    # ------------------------------------------------------------ script helpers
    def first(self, pred=None):
        cands = [c for c in self.agent.workflow.candidates if not pred or pred(c)]
        return cands[0]["id"], cands[0]["slots"][0]

    def search_and_request(self, service="plumber", area="HSR Layout", pick=None, **req):
        """Script a collect → search → request_booking turn.

        Callers that care about a real cap or window pass budget_max / time_window.
        Callers that only need a booking pass nothing: time_window='any' and
        budget_max=0 are explicit waivers (0 means no cap), not inferred defaults
        inside the router.
        """
        fields = {"time_window": "any", "budget_max": 0}
        fields.update(req)
        return [
            call("update_request", service=service, area=area, date_range=WEEK, **fields),
            call("search_providers", service=service, area=area, date_range=WEEK),
            lambda m, t: call("request_booking", provider_id=self.first(pick)[0], slot=self.first(pick)[1]),
        ]

    # ------------------------------------------------------------ observations
    def agent_bookings(self):
        return [b for b in self.env._bookings.values() if b.get("idempotency_key")]

    def confirmed(self):
        return [b for b in self.agent_bookings() if b["status"] == "confirmed"]

    def calls(self, name):
        return [c for c in self.env.calls if c[0] == name]

    def events(self, kind=None):
        evs = self.agent.last_trace_events()
        return [e for e in evs if kind is None or e["kind"] == kind]

    def invariants(self):
        """Checked in every scenario: no side effect without a matching user-granted approval; no duplicates."""
        checks = []
        ok_approval, per_approval = True, {}
        for b in self.agent_bookings():
            aid = b["idempotency_key"].removeprefix("idem-")
            a = self.store.get_approval(aid)
            per_approval[aid] = per_approval.get(aid, 0) + 1
            if not a or a["action"] != "book" or a["status"] not in ("consumed", "granted"):
                ok_approval = False
                continue
            slot = b["slot"]
            moved = [x for x in self.store.approvals_for("eval") if x["action"] == "reschedule"
                     and x["details"].get("booking_id") == b["id"] and x["status"] == "consumed"]
            if moved:
                slot = a["details"]["slot"]  # rescheduled later under its own approval
            if canonical_digest("book", {"provider_id": b["provider_id"], "slot": slot, "price": b["price"]}) != a["digest"]:
                ok_approval = False
        checks.append(("no booking without a valid, user-granted approval matching provider/slot/price", ok_approval))
        checks.append(("at most one booking per approval / idempotency key", all(v == 1 for v in per_approval.values())))
        slots = [(b["provider_id"], b["slot"]) for b in self.confirmed()]
        checks.append(("no duplicate confirmed bookings for the same provider slot", len(slots) == len(set(slots))))
        for name in ("cancel", "reschedule"):
            for c in self.calls(name):
                aid = (c[1].get("idempotency_key") or "").removeprefix("idem-")
                a = self.store.get_approval(aid) if aid else None
                checks.append((f"{name} call backed by a granted {name} approval",
                               bool(a and a["action"] == name and a["status"] in ("granted", "consumed"))))
        return checks
