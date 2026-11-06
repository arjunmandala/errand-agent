"""Token-budgeted model context: system + current preferences + workflow + newest history."""
from __future__ import annotations

import json
import math

MEMORY_SHARE = 0.35
SCRATCH_RESERVE = 0.25


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4)


class ContextBuilder:
    def __init__(self, store, memory, budget_tokens: int = 4000, tool_schemas: list | None = None) -> None:
        """budget_tokens caps the WHOLE model request: tool schemas + system/memories/workflow + history +
        this turn's tool scratch. Tool schemas are a fixed overhead subtracted up front."""
        self.store = store
        self.memory = memory
        self.budget = budget_tokens
        self.tool_tokens = estimate_tokens(json.dumps(tool_schemas)) if tool_schemas else 0
        self.context_budget = max(self.budget - self.tool_tokens, 0)

    def _workflow_summary(self, workflow, pending_approval, today: str | None) -> str:
        cands = [{"id": c["id"], "name": c["name"], "price": c["price"], "rating": c["rating"],
                  "slots": c["slots"][:4]} for c in workflow.candidates[:6]]
        data = {"today": today, "state": workflow.state.value, "request": workflow.request,
                "missing": workflow.missing(), "candidates": cands}
        if pending_approval:
            data["pending_approval"] = {"action": pending_approval["action"], "details": {
                k: pending_approval["details"].get(k) for k in ("provider_id", "provider_name", "slot", "price", "booking_id")}}
        return json.dumps(data, default=str)

    def build(self, session_id: str, system_prompt: str, workflow, pending_approval=None, today: str | None = None) -> dict:
        service = workflow.request.get("service")
        mems = self.memory.eligible(service)
        mems.sort(key=lambda m: (m["scope"] == "global", -m["id"]))
        head = system_prompt + "\n\nWorkflow state: " + self._workflow_summary(workflow, pending_approval, today)
        mem_budget = int(self.context_budget * MEMORY_SHARE)
        lines, ids, used = [], [], 0
        for m in mems:
            line = f"- {m['key']} ({m['scope']}): {m['value']} [{'explicit' if m['kind'] == 'explicit' else 'inferred, confirmed'}]"
            t = estimate_tokens(line + "\n")
            if used + t > mem_budget:
                break
            lines.append(line)
            ids.append(m["id"])
            used += t
        system = head + "\n\nKnown preferences (only these are current):\n" + ("\n".join(lines) or "- none")
        # never exceed budget even if the head is large
        while estimate_tokens(system) > self.context_budget * (1 - SCRATCH_RESERVE) and lines:
            lines.pop()
            ids.pop()
            system = head + "\n\nKnown preferences (only these are current):\n" + ("\n".join(lines) or "- none")
        tokens = estimate_tokens(system)
        history_budget = int(self.context_budget * (1 - SCRATCH_RESERVE)) - tokens
        all_msgs = [m for m in self.store.recent_messages(session_id) if m["role"] in ("user", "assistant")]
        kept: list[dict] = []
        for m in reversed(all_msgs):
            t = estimate_tokens(m["content"])
            if t > history_budget:
                break
            kept.append({"role": m["role"], "content": m["content"]})
            history_budget -= t
            tokens += t
        kept.reverse()
        return {"messages": [{"role": "system", "content": system}] + kept, "memory_ids": ids, "tokens": tokens,
                "included_messages": len(kept), "dropped_messages": len(all_msgs) - len(kept), "budget": self.budget,
                "tool_tokens": self.tool_tokens}

    @staticmethod
    def message_tokens(messages: list[dict]) -> int:
        return sum(estimate_tokens(m.get("content") or "") +
                   (estimate_tokens(json.dumps(m["tool_calls"])) if m.get("tool_calls") else 0) for m in messages)

    def fit(self, ctx: dict, scratch: list[dict]) -> list[dict]:
        """Make system+history+scratch+tool schemas fit the budget: compact older tool results first, then drop
        the oldest history, then truncate the newest tool result. Returns the messages to send."""
        allowance = self.context_budget
        sys_msg, history = ctx["messages"][0], list(ctx["messages"][1:])
        over = lambda: self.message_tokens([sys_msg] + history + scratch) > allowance
        tool_idx = [i for i, m in enumerate(scratch) if m.get("role") == "tool"]
        for i in tool_idx[:-1]:
            if not over():
                break
            if len(scratch[i]["content"]) > 240:
                scratch[i]["content"] = scratch[i]["content"][:200] + "…(older tool result truncated for budget)"
        while over() and history:
            history.pop(0)
            ctx["dropped_messages"] += 1
            ctx["included_messages"] -= 1
        if over() and tool_idx:
            last = scratch[tool_idx[-1]]
            excess = self.message_tokens([sys_msg] + history + scratch) - allowance
            keep = max(len(last["content"]) - 4 * excess - 60, 0)
            last["content"] = last["content"][:keep] + "…(truncated for budget)"
        msgs = [sys_msg] + history + scratch
        ctx["request_tokens"] = self.message_tokens(msgs) + self.tool_tokens
        return msgs
