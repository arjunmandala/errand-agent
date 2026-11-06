"""Executes model tool calls. Read-only tools pass through (sanitised); request_* tools hold a slot and open
an approval that only the user can grant. Returns (result_for_model, stop_reply_or_None)."""
from __future__ import annotations

import time

from errand_agent.sanitize import redact, sanitize_tool_result
from errand_agent.tools import TOOL_NAMES
from errand_agent.workflow import State

FORGET_WORDS = ("forget", "remove", "delete", "stop remembering", "don't remember", "dont remember")
WINDOWS = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


class Router:
    def __init__(self, agent) -> None:
        self.a = agent

    # ---------------------------------------------------------------- helpers
    def call_env(self, name: str, **args):
        """Call a mock tool with tracing; transport errors become structured errors."""
        t0 = time.perf_counter()
        err = None
        try:
            res = getattr(self.a.env, name)(**args)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            res = {"error": "tool_failure", "detail": err}
        if self.a.tracer:
            self.a.tracer.event("provider_call", name=name, args=args, source="app",
                                result=sanitize_tool_result(name, res),
                                latency_ms=round((time.perf_counter() - t0) * 1000, 2), error=err)
        return res

    @staticmethod
    def apply_filters(providers: list[dict], c: dict) -> list[dict]:
        """App-side constraint filter: budget, time window, earliest time."""
        out = []
        lo, hi = WINDOWS.get(c.get("time_window") or "", (0, 24))
        earliest = c.get("earliest_time")
        not_before = c.get("not_before") or ""
        for p in providers:
            if c.get("budget_max") and p["price"] > int(c["budget_max"]):
                continue
            slots = [s for s in p["slots"] if lo <= int(s[11:13]) < hi and (not earliest or s[11:16] >= earliest)
                     and s > not_before]
            if slots:
                out.append({k: p[k] for k in ("id", "name", "price", "rating", "attributes")} | {"slots": slots})
        return out

    def _known(self) -> dict:
        r = dict(self.a.workflow.request)
        service = r.get("service")
        for field, key in (("area", "preferred_area"), ("earliest_time", "earliest_time"), ("budget_max", "budget_max")):
            if field not in r:
                v = self.a.memory.get(key, service)
                if v is not None:
                    r[field] = v
        return r

    def _open_approval(self, action: str, details: dict):
        a = self.a
        approval = a.approvals.request(a.session_id, action, details)
        a.workflow.active_approval_id = approval["id"]
        a.goto(State.AWAITING_APPROVAL)
        a.tracer.event("approval", event="requested", approval_id=approval["id"], action=action,
                       digest=approval["digest"], details=details)
        from errand_agent.agent import Reply, render_approval
        return ({"status": "awaiting_user_approval", "approval_id": approval["id"]},
                Reply(text=render_approval(approval), kind="approval_request", approval=approval))

    def _own_booking(self, booking_id: str):
        for b in self.a.env.list_bookings(self.a.user_id)["bookings"]:
            if b["id"] == booking_id:
                return b
        return None

    # ---------------------------------------------------------------- dispatch
    def run(self, name: str, args: dict):
        t0 = time.perf_counter()
        err = None
        try:
            if name not in TOOL_NAMES:
                result, stop = {"error": "unknown_tool", "allowed": TOOL_NAMES}, None
            else:
                result, stop = getattr(self, "t_" + name)(**(args or {}))
        except TypeError as e:
            result, stop, err = {"error": "bad_arguments", "detail": str(e)}, None, str(e)
        except Exception as e:
            result, stop, err = {"error": "tool_failure", "detail": f"{type(e).__name__}"}, None, f"{type(e).__name__}: {e}"
        result = sanitize_tool_result(name, result)
        self.a.tracer.event("tool_call", name=name, args=args, source="model", result=redact(result),
                            latency_ms=round((time.perf_counter() - t0) * 1000, 2), error=err)
        return result, stop

    # ---------------------------------------------------------------- tools
    def t_update_request(self, **fields):
        wf = self.a.workflow
        before = dict(wf.request)
        wf.update(**fields)
        changed = {k: v for k, v in wf.request.items() if before.get(k) != v}
        pending = self.a.store.pending_approval(self.a.session_id)
        if changed and pending:
            self.a.approvals.invalidate(pending["id"], "request_changed")
            self.a.tracer.event("approval", event="invalidated", approval_id=pending["id"], reason="request_changed",
                                changed=changed)
            self.a.goto(State.COLLECTING)
        return {"request": wf.request, "missing": wf.missing(), "changed": sorted(changed)}, None

    def t_search_providers(self, service=None, area=None, date_range=None):
        wf = self.a.workflow
        wf.update(service=service, area=area, date_range=date_range)
        if wf.missing():
            return {"error": "missing_fields", "missing": wf.missing()}, None
        r = wf.request
        self.a.goto(State.SEARCHING)
        res = self.call_env("search_providers", service=r["service"], area=r["area"], date_range=r["date_range"])
        if res.get("error"):
            self.a.goto(State.COLLECTING)
            return res, None
        known = self._known()
        constraints = {"budget_max": known.get("budget_max") if "budget_max" in r else r.get("budget_max"),
                       "time_window": r.get("time_window"), "earliest_time": known.get("earliest_time"),
                       "not_before": self.a.clock.now().strftime("%Y-%m-%dT%H:%M")}
        cands = self.apply_filters(res["providers"], constraints)
        for c in cands:
            c["area"] = r["area"]
        wf.candidates = cands
        self.a.goto(State.PROPOSING)
        return {"providers": [c | {"slots": c["slots"][:6]} for c in cands],
                "filtered_out": len(res["providers"]) - len(cands), "constraints_applied": constraints}, None

    def t_get_provider(self, provider_id):
        return self.call_env("get_provider", provider_id=provider_id), None

    def t_list_bookings(self):
        return self.call_env("list_bookings", user_id=self.a.user_id), None

    def t_get_booking_status(self, hold_id=None, idempotency_key=None):
        return self.call_env("get_booking_status", hold_id=hold_id, idempotency_key=idempotency_key), None

    def t_request_booking(self, provider_id, slot):
        wf = self.a.workflow
        cand = next((c for c in wf.candidates if c["id"] == provider_id), None)
        if cand is None or slot not in cand["slots"]:
            return {"error": "unknown_candidate", "detail": "provider/slot must come from the latest search results "
                    "that fit the user's constraints"}, None
        hold = self.call_env("hold_slot", provider_id=provider_id, slot=slot)
        if hold.get("error"):
            return hold, None
        details = {"provider_id": provider_id, "provider_name": cand["name"], "service": wf.request.get("service"),
                   "area": wf.request.get("area"), "slot": slot, "price": hold["quoted_price"],
                   "search_price": cand["price"], "hold_id": hold["hold_id"], "hold_expires_at": hold["expires_at"]}
        return self._open_approval("book", details)

    def t_request_cancel(self, booking_id):
        b = self._own_booking(booking_id)
        if not b or b["status"] != "confirmed":
            return {"error": "not_cancellable", "detail": "no confirmed booking with that id"}, None
        return self._open_approval("cancel", {"booking_id": booking_id, "provider_id": b["provider_id"],
                                              "provider_name": b["provider_name"], "service": b["service"],
                                              "slot": b["slot"]})

    def t_request_reschedule(self, booking_id, slot):
        b = self._own_booking(booking_id)
        if not b or b["status"] != "confirmed":
            return {"error": "not_reschedulable", "detail": "no confirmed booking with that id"}, None
        return self._open_approval("reschedule", {"booking_id": booking_id, "provider_id": b["provider_id"],
                                                  "provider_name": b["provider_name"], "service": b["service"],
                                                  "old_slot": b["slot"], "slot": slot})

    def t_ask_user(self, question, fields=None):
        known = self._known()
        fields = list(fields or [])
        already = {f: known[f] for f in fields if f in known}
        if fields and len(already) == len(fields):
            return {"error": "already_known", "known": already, "hint": "do not re-ask; ask only missing fields",
                    "missing": self.a.workflow.missing()}, None
        from errand_agent.agent import Reply
        return {"status": "asked"}, Reply(text=question, kind="message")

    def t_remember_preference(self, key, value, scope="global"):
        said = str(value).lower() in (self.a.current_user_text or "").lower()
        kind = "explicit" if said else "inferred"
        row = self.a.memory.remember(key, value, kind=kind, scope=scope or "global",
                                     source="user" if said else "model")
        note = "" if said else " (stored as inferred and unconfirmed: the user did not state this value)"
        return {"saved": {k: row[k] for k in ("id", "key", "value", "kind", "confirmed", "scope")}, "note": note}, None

    def t_forget_preference(self, key):
        text = (self.a.current_user_text or "").lower()
        if not any(w in text for w in FORGET_WORDS):
            return {"error": "user_did_not_ask_to_forget"}, None
        return {"forgotten": self.a.memory.forget(key)}, None

    def t_list_memories(self):
        rows = self.a.memory.eligible(self.a.workflow.request.get("service"))
        return {"memories": [{k: m[k] for k in ("key", "value", "kind", "scope")} for m in rows]}, None
