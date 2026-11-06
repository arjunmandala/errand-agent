"""Model adapters: ScriptedModel (offline evals) and XAIModel (live, xAI Responses API)."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

GROK_AGENT_PATH = os.environ.get("GROK_AGENT_PY", "/home/box/tools/grok-agent/grok_agent.py")
TOKEN_FILE = "/home/box/secrets/xai_bearer"


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class ModelResponse:
    text: str | None = None
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None


class ModelError(Exception):
    pass


class ScriptedModel:
    """Deterministic stand-in for an LLM. Steps are ModelResponse or callables(messages, tools)."""

    name = "scripted"

    def __init__(self, script=None) -> None:
        self.script = list(script or [])
        self.calls: list[list[dict]] = []

    def extend(self, steps) -> None:
        self.script.extend(steps)

    def complete(self, messages, tools) -> ModelResponse:
        self.calls.append(copy.deepcopy(messages))
        if not self.script:
            return ModelResponse(text="(scripted model has no more steps)")
        step = self.script.pop(0)
        resp = step(messages, tools) if callable(step) else step
        if resp.cost_usd is None:
            resp.cost_usd = 0.0  # offline: no spend
        return resp


def discover_token() -> str:
    """Same bearer the shared grok-agent CLI uses (pi xAI login → USER_BEARER_TOKEN → token file → USER_LLM_API_KEY),
    or XAI_API_KEY. Never printed or logged."""
    if os.environ.get("XAI_API_KEY"):
        return "".join(os.environ["XAI_API_KEY"].split())
    if os.path.isfile(GROK_AGENT_PATH):
        try:
            spec = importlib.util.spec_from_file_location("_grok_agent_shared", GROK_AGENT_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.get_token()
        except SystemExit:
            pass
        except Exception:
            pass
    for env in ("USER_BEARER_TOKEN",):
        if os.environ.get(env):
            return "".join(os.environ[env].split())
    if os.path.isfile(TOKEN_FILE):
        return "".join(open(TOKEN_FILE).read().split())
    raise ModelError("No xAI token found (pi xAI login, USER_BEARER_TOKEN, /home/box/secrets/xai_bearer or XAI_API_KEY).")


def to_responses_input(messages: list[dict]) -> list[dict]:
    """Our chat messages → xAI Responses API input items."""
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
        elif role == "assistant" and m.get("tool_calls"):
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for tc in m["tool_calls"]:
                items.append({"type": "function_call", "call_id": tc["id"], "name": tc["name"],
                              "arguments": json.dumps(tc["args"])})
        else:
            items.append({"role": role, "content": m.get("content") or ""})
    return items


def to_responses_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "name": t["name"], "description": t.get("description", ""),
             "parameters": t["parameters"]} for t in tools]


class XAIModel:
    """Live model. POST {base_url}/responses with function tools; stateless (store=false)."""

    def __init__(self, model: str = "grok-4.7", base_url: str = "https://api.x.ai/v1", timeout: int = 60,
                 reasoning_effort: str | None = "low") -> None:
        self.name = model
        self.base_url = base_url
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self._token = discover_token()

    def complete(self, messages, tools) -> ModelResponse:
        payload = {"model": self.name, "input": to_responses_input(messages),
                   "tools": to_responses_tools(tools), "store": False}
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        req = urllib.request.Request(self.base_url + "/responses", data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self._token})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace").replace(self._token, "[REDACTED]")
            raise ModelError(f"xAI HTTP {e.code}: {body[:300]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ModelError(f"xAI network error: {e}") from None
        return parse_responses_output(resp)


def parse_responses_output(resp: dict) -> ModelResponse:
    text_parts, calls = [], []
    for item in resp.get("output", []):
        if item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": item.get("arguments")}
            calls.append(ToolCall(id=item.get("call_id") or item.get("id"), name=item["name"], args=args))
        elif item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    text_parts.append(c.get("text", ""))
    u = resp.get("usage") or {}
    cost = int(u["cost_in_usd_ticks"]) / 1e10 if u.get("cost_in_usd_ticks") else None
    return ModelResponse(text="".join(text_parts) or None, tool_calls=calls,
                         usage={"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)},
                         cost_usd=cost)
