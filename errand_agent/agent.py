"""Errand Agent: a deterministic shell (approvals, executor, limits, memory, tracing) around a model
that can only read and *request* consequential actions."""
from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from errand_agent.approval import ApprovalError, Approvals, parse_decision
from errand_agent.context import ContextBuilder
from errand_agent.executor import Executor
from errand_agent.memory import Memory
from errand_agent.router import Router
from errand_agent.tools import SYSTEM_PROMPT, TOOL_SCHEMAS
from errand_agent.trace import Tracer
from errand_agent.workflow import TRANSITIONS, State, Workflow

SUCCESS_CLAIM = re.compile(r"\b(booked|confirmed|cancelled|canceled|rescheduled|all set)\b", re.IGNORECASE)
SAFE_NO_CLAIM = "Nothing has been reserved or changed yet; I only report a booking after the booking service confirms it. Want me to look for options?"


@dataclass
class Limits:
    max_model_turns: int = 6
    max_tool_calls: int = 12
    wall_clock_s: float = 45.0


@dataclass
class Reply:
    text: str
    kind: str = "message"  # message | approval_request | outcome | notification | error | limit
    approval: dict | None = None
    outcome: dict | None = None
    trace_path: str | None = None


def fmt_slot(slot: str) -> str:
    try:
        return datetime.fromisoformat(slot).strftime("%a %d %b, %H:%M")
    except (TypeError, ValueError):
        return str(slot)


def render_approval(approval: dict) -> str:
    """The app (not the model) renders the exact material details the user approves."""
    d, action = approval["details"], approval["action"]
    who = f"{d.get('provider_name', d.get('provider_id'))} ({d.get('provider_id')})"
    if action == "book":
        note = ""
        if d.get("search_price") is not None and d["search_price"] != d["price"]:
            note = f" Note: the quoted price is ₹{d['price']}, search showed ₹{d['search_price']}."
        exp = f" The slot is held until {fmt_slot(d['hold_expires_at'])[-5:]}." if d.get("hold_expires_at") else ""
        return (f"Please confirm: book {who} for {d.get('service')} on {fmt_slot(d['slot'])} at ₹{d['price']}"
                f"{' in ' + d['area'] if d.get('area') else ''}.{note}{exp} Reply \"yes\" to confirm or \"no\" to cancel.")
    if action == "cancel":
        return (f"Please confirm: cancel booking {d['booking_id']} with {who} on {fmt_slot(d['slot'])}. "
                "Reply \"yes\" to confirm or \"no\" to keep it.")
    return (f"Please confirm: move booking {d['booking_id']} with {who} from {fmt_slot(d['old_slot'])} to "
            f"{fmt_slot(d['slot'])}. Reply \"yes\" to confirm or \"no\" to keep the current slot.")


class Agent:
    def __init__(self, env, model, store, clock, session_id: str = "default", user_id: str = "arjun",
                 limits: Limits | None = None, trace_dir="traces", context_budget: int = 1800) -> None:
        self.env, self.model, self.store, self.clock = env, model, store, clock
        self.session_id, self.user_id = session_id, user_id
        self.limits = limits or Limits()
        self.trace_dir = trace_dir
        self.memory = Memory(store, clock)
        self.approvals = Approvals(store, clock)
        self.executor = Executor(env, store, self.approvals, clock)
        self.context = ContextBuilder(store, self.memory, context_budget)
        self.router = Router(self)
        saved = store.load_workflow(session_id)
        self.workflow = Workflow.from_dict(saved) if saved else Workflow()
        self.tracer: Tracer | None = None
        store.conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
        store.conn.commit()

    # ------------------------------------------------------------------ plumbing
    def _new_tracer(self, reason: str, **data) -> Tracer:
        self.tracer = Tracer(self.trace_dir, self.session_id, self.clock)
        self.executor.tracer = self.tracer
        self.tracer.event("run_start", reason=reason, session_id=self.session_id, model=getattr(self.model, "name", "?"),
                          state=self.workflow.state.value, limits=vars(self.limits), **data)
        return self.tracer

    def last_trace_events(self) -> list[dict]:
        return self.tracer.events if self.tracer else []

    def goto(self, target: State) -> None:
        """Move to target via the shortest whitelisted path, tracing every hop."""
        if self.workflow.state == target:
            return
        path = self._path(self.workflow.state, target)
        for step in path:
            prev = self.workflow.state
            self.workflow.transition(step)
            if self.tracer:
                self.tracer.event("state_transition", **{"from": prev.value, "to": step.value})

    @staticmethod
    def _path(src: State, dst: State) -> list[State]:
        prev = {src: None}
        q = deque([src])
        while q:
            s = q.popleft()
            if s == dst:
                break
            for n in TRANSITIONS[s] | {State.COLLECTING, State.FAILED}:
                if n not in prev:
                    prev[n] = s
                    q.append(n)
        out, cur = [], dst
        while cur != src:
            out.append(cur)
            cur = prev[cur]
        return list(reversed(out))

    def kv_get(self, key: str):
        r = self.store.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def kv_set(self, key: str, value) -> None:
        self.store.conn.execute("INSERT OR REPLACE INTO kv(key,value) VALUES (?,?)", (key, json.dumps(value)))
        self.store.conn.commit()

    def _finish(self, reply: Reply, save_message: bool = True) -> Reply:
        if save_message:
            self.store.add_message(self.session_id, "assistant", reply.text)
        self.store.save_workflow(self.session_id, self.workflow.to_dict())
        if self.tracer:
            self.tracer.event("run_end", reply_kind=reply.kind, text=reply.text, state=self.workflow.state.value,
                              outcome_status=(reply.outcome or {}).get("status"))
            reply.trace_path = str(self.tracer.path)
        return reply

    # ------------------------------------------------------------------ entry points
    def start(self) -> list[Reply]:
        """Process start: resolve side effects left pending by a crash before talking to anyone."""
        self._new_tracer("startup_recovery")
        replies = []
        for o in self.executor.recover():
            self.tracer.event("recovery", outcome=o)
            if o["status"] == "confirmed":
                self.goto(State.DONE)
                text = "After a restart I checked the booking service: " + o["message"]
            elif o["status"] == "cancelled":
                text = "After a restart I checked: " + o["message"]
                self.goto(State.DONE)
            else:
                text = o["message"]
                self.goto(State.COLLECTING)
            replies.append(self._finish(Reply(text=text, kind="outcome", outcome=o)))
        if not replies:
            self._finish(Reply(text="(nothing to recover)", kind="message"), save_message=False)
        return replies

    def handle(self, user_text: str) -> Reply:
        self._new_tracer("user_message", user_text=user_text)
        self.store.add_message(self.session_id, "user", user_text)
        self.current_user_text = user_text
        self.run_outcomes: list[dict] = []
        try:
            if user_text.strip().startswith("/"):
                return self._finish(self._command(user_text.strip()))
            pending = self.store.pending_approval(self.session_id)
            if pending:
                decision = parse_decision(user_text)
                self.tracer.event("approval", event="decision_parsed", approval_id=pending["id"], decision=decision)
                if decision == "confirm":
                    return self._finish(self._execute(pending, user_text))
                if decision == "reject":
                    self.approvals.reject(pending["id"])
                    self.tracer.event("approval", event="rejected", approval_id=pending["id"])
                    self.goto(State.PROPOSING if self.workflow.candidates and pending["action"] == "book"
                              else State.COLLECTING)
                    return self._finish(Reply(text="Okay, I won't go ahead. Nothing was booked or changed.",
                                              kind="message"))
            return self._finish(self._model_loop())
        except Exception as e:  # every exit path produces a user-facing outcome
            self.tracer.event("error", error=f"{type(e).__name__}: {e}")
            return self._finish(Reply(text="Something went wrong on my side, so I stopped. Nothing was booked or "
                                           "changed unless I said so above.", kind="error"))

    # ------------------------------------------------------------------ approvals → executor
    def _execute(self, pending: dict, user_text: str) -> Reply:
        try:
            self.approvals.grant(pending["id"], user_text)
        except ApprovalError as e:
            self.tracer.event("approval", event="grant_failed", approval_id=pending["id"], error=str(e))
            self.goto(State.COLLECTING)
            return Reply(text="That approval has expired, so I didn't do anything. Want me to check again?",
                         kind="message")
        self.tracer.event("approval", event="granted", approval_id=pending["id"], digest=pending["digest"])
        self.goto(State.EXECUTING)
        action = pending["action"]
        fn = {"book": self.executor.book, "cancel": self.executor.cancel, "reschedule": self.executor.reschedule}[action]
        out = fn(pending["id"])
        self.run_outcomes.append(out)
        self.workflow.last_outcome = {k: v for k, v in out.items() if k != "new_approval"}
        self.tracer.event("approval", event="executed", approval_id=pending["id"], status=out["status"])
        status = out["status"]
        if status == "needs_reapproval":
            new = out["new_approval"]
            self.workflow.active_approval_id = new["id"]
            self.goto(State.AWAITING_APPROVAL)
            self.tracer.event("approval", event="requested", approval_id=new["id"], details=new["details"],
                              reason=out.get("reason"))
            return Reply(text="The hold expired and the provider re-quoted a different price, so your earlier "
                              "approval no longer applies. " + render_approval(new), kind="approval_request",
                         approval=new, outcome=out)
        if status in ("confirmed", "cancelled"):
            self.goto(State.VERIFYING)
            self.goto(State.DONE)
            return Reply(text=self._success_text(action, pending["details"], out), kind="outcome", outcome=out)
        self.goto(State.FAILED)
        self.goto(State.COLLECTING)
        text = out.get("message") or "That didn't go through."
        if out.get("reason") == "slot_taken":
            text = "That slot was taken just before I could book it, so nothing was booked. Want me to pick another slot?"
        elif out.get("reason") == "slot_conflict":
            text = "That new slot conflicts with the provider's schedule, so your booking is unchanged. Want a different slot?"
        return Reply(text=text, kind="outcome", outcome=out)

    def _success_text(self, action: str, d: dict, out: dict) -> str:
        if action == "book":
            b = out["booking"]
            via = " (the booking call timed out, so I verified it with the booking service)" \
                if out.get("verified_via") == "get_booking_status" else ""
            return (f"Booked: {d.get('provider_name')} on {fmt_slot(d['slot'])} at ₹{d['price']}. "
                    f"Booking ID {b['booking_id']}{via}.")
        if action == "cancel":
            return f"Cancelled booking {d['booking_id']}."
        return f"Moved booking {d['booking_id']} to {fmt_slot(d['slot'])}."

    # ------------------------------------------------------------------ model loop
    def _limit(self, which: str) -> Reply:
        self.tracer.event("limit_hit", limit=which)
        summary = f"Current step: {self.workflow.state.value}"
        if self.workflow.request:
            summary += f", request so far: {json.dumps(self.workflow.request)}"
        return Reply(text=f"I stopped after hitting my safety limit ({which}) before finishing. Nothing was booked "
                          f"or changed. {summary}. Tell me how you'd like to continue.", kind="limit")

    def _model_loop(self) -> Reply:
        started = self.clock.now()
        turns = tool_calls = 0
        scratch: list[dict] = []
        while True:
            if turns >= self.limits.max_model_turns:
                return self._limit(f"{self.limits.max_model_turns} model turns")
            elapsed = (self.clock.now() - started).total_seconds()
            if elapsed >= self.limits.wall_clock_s:
                return self._limit(f"{int(self.limits.wall_clock_s)}s wall clock")
            ctx = self.context.build(self.session_id, SYSTEM_PROMPT, self.workflow,
                                     self.store.pending_approval(self.session_id), today=self.clock.now().isoformat())
            self.tracer.event("context", turn=turns, memory_ids=ctx["memory_ids"], tokens=ctx["tokens"],
                              included_messages=ctx["included_messages"], dropped_messages=ctx["dropped_messages"],
                              budget=ctx["budget"])
            messages = ctx["messages"] + scratch
            t0 = time.perf_counter()
            try:
                resp = self.model.complete(messages, TOOL_SCHEMAS)
            except Exception as e:
                self.tracer.event("model_call", turn=turns, error=str(e), latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                                  input_tokens=0, output_tokens=0, cost_usd=0.0)
                return Reply(text=f"Sorry, I couldn't reach the language model ({type(e).__name__}). "
                                  "Nothing was booked or changed.", kind="error")
            turns += 1
            self.tracer.event("model_call", turn=turns, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                              input_tokens=resp.usage.get("input_tokens", 0),
                              output_tokens=resp.usage.get("output_tokens", 0), cost_usd=resp.cost_usd,
                              tool_calls=[tc.name for tc in resp.tool_calls], text=resp.text)
            if not resp.tool_calls:
                return self._guarded_text(resp.text or "")
            scratch.append({"role": "assistant", "content": resp.text or "",
                            "tool_calls": [{"id": tc.id, "name": tc.name, "args": tc.args} for tc in resp.tool_calls]})
            for tc in resp.tool_calls:
                if tool_calls >= self.limits.max_tool_calls:
                    return self._limit(f"{self.limits.max_tool_calls} tool calls")
                tool_calls += 1
                result, stop = self.router.run(tc.name, tc.args)
                scratch.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                "content": json.dumps(result, default=str)})
                if stop is not None:
                    return stop

    def _guarded_text(self, text: str) -> Reply:
        verified = any(o.get("status") in ("confirmed", "cancelled") for o in self.run_outcomes)
        if SUCCESS_CLAIM.search(text) and not verified and not self._claim_is_historical(text):
            self.tracer.event("blocked_claim", text=text)
            return Reply(text=SAFE_NO_CLAIM, kind="message")
        return Reply(text=text or "(no reply)", kind="message")

    def _claim_is_historical(self, text: str) -> bool:
        """Allow mentioning a booking that the ledger already has as confirmed (e.g. 'already booked BK-..')."""
        ids = {b.get("booking_id") for b in self.store.bookings()}
        return any(i and i in text for i in ids)

    # ------------------------------------------------------------------ slash commands
    def _command(self, cmd: str) -> Reply:
        name, _, arg = cmd.partition(" ")
        arg = arg.strip()
        self.tracer.event("command", command=name, arg=arg)
        if name == "/memory":
            rows = self.memory.list()
            if not rows:
                return Reply(text="I don't have any stored preferences.")
            lines = []
            for m in rows:
                kind = "explicit" if m["kind"] == "explicit" else (
                    "inferred (confirmed)" if m["confirmed"] else "inferred (unconfirmed, not used)")
                lines.append(f"- {m['key']} = {m['value']} [{kind}, scope {m['scope']}]")
            return Reply(text="Stored preferences:\n" + "\n".join(lines))
        if name in ("/remember", "/correct") and "=" in arg:
            key, value = (x.strip() for x in arg.split("=", 1))
            scope = "global"
            if ":" in key:
                scope, key = key.split(":", 1)
            self.memory.remember(key, value, kind="explicit", scope=scope, source="user")
            return Reply(text=f"Saved {key} = {value}.")
        if name == "/forget" and arg:
            n = self.memory.forget(arg)
            return Reply(text=f"Forgot {arg}." if n else f"I had nothing stored for {arg}.")
        if name == "/confirm" and arg:
            n = self.memory.confirm(arg)
            return Reply(text=f"Confirmed {arg}." if n else f"No inferred preference named {arg}.")
        return Reply(text="Commands: /memory, /remember key=value, /correct key=value, /forget key, /confirm key")

    # ------------------------------------------------------------------ proactive follow-up
    def tick(self) -> list[Reply]:
        """Poll for provider-side cancellations and act without a user prompt."""
        handled = set(self.kv_get("handled_provider_cancellations") or [])
        bookings = self.env.list_bookings(self.user_id)["bookings"]
        new = [b for b in bookings if b["status"] == "cancelled_by_provider" and b["id"] not in handled]
        if not new:
            return []
        self._new_tracer("tick", detected=[b["id"] for b in new])
        self.current_user_text = ""
        self.run_outcomes = []
        replies = []
        for b in new:
            handled.add(b["id"])
            self.kv_set("handled_provider_cancellations", sorted(handled))
            self.tracer.event("provider_cancellation_detected", booking=b)
            if b["slot"] <= self.clock.now().strftime("%Y-%m-%dT%H:%M"):
                self.tracer.event("notify_decision", booking_id=b["id"], notify=False, reason="slot in the past")
                continue
            self.tracer.event("notify_decision", booking_id=b["id"], notify=True, reason="upcoming booking")
            replies.append(self._finish(self._replace(b)))
        return replies

    def _replace(self, b: dict) -> Reply:
        ledger = {x["booking_id"]: x for x in self.store.bookings()}.get(b["id"], {})
        service = b["service"]
        req = self.workflow.request if self.workflow.request.get("service") == service else {}
        area = ledger.get("area") or req.get("area") or self.memory.get("preferred_area", service) or "HSR Layout"
        day = datetime.fromisoformat(b["slot"]).date()
        date_range = [day.isoformat(), (day + timedelta(days=3)).isoformat()]
        budget = req.get("budget_max") or self.memory.get("budget_max", service)
        constraints = {"budget_max": int(budget) if budget else None, "time_window": req.get("time_window"),
                       "earliest_time": req.get("earliest_time") or self.memory.get("earliest_time", service),
                       "not_before": self.clock.now().strftime("%Y-%m-%dT%H:%M")}
        head = (f"Heads up: {b['provider_name']} cancelled your {service} booking on {fmt_slot(b['slot'])}. ")
        found = self.env.search_providers(service, area, date_range)
        self.tracer.event("tool_call", name="search_providers", args={"service": service, "area": area,
                          "date_range": date_range}, result={"count": len(found.get("providers", []))})
        cands = [c for c in self.router.apply_filters(found.get("providers", []), constraints)
                 if c["id"] != b["provider_id"]]
        orig = datetime.fromisoformat(b["slot"])

        def score(c):
            gap = min(abs((datetime.fromisoformat(s) - orig).total_seconds()) for s in c["slots"])
            return (gap, -c["rating"], c["price"])

        for c in sorted(cands, key=score):
            slot = min(c["slots"], key=lambda s: abs((datetime.fromisoformat(s) - orig).total_seconds()))
            hold = self.router.call_env("hold_slot", provider_id=c["id"], slot=slot)
            if hold.get("error"):
                continue
            details = {"provider_id": c["id"], "provider_name": c["name"], "service": service, "area": area,
                       "slot": slot, "price": hold["quoted_price"], "search_price": c["price"],
                       "hold_id": hold["hold_id"], "hold_expires_at": hold["expires_at"], "replaces": b["id"]}
            if constraints["budget_max"] and details["price"] > constraints["budget_max"]:
                continue
            approval = self.approvals.request(self.session_id, "book", details)
            self.workflow.active_approval_id = approval["id"]
            self.workflow.candidates = cands
            self.goto(State.AWAITING_APPROVAL)
            self.tracer.event("approval", event="requested", approval_id=approval["id"], details=details,
                              reason="replacement")
            return Reply(text=head + "I found a replacement within your preferences. " + render_approval(approval),
                         kind="approval_request", approval=approval)
        return Reply(text=head + "I couldn't find a replacement that fits your preferences in the next few days. "
                                 "Want me to widen the dates or budget?", kind="notification")
