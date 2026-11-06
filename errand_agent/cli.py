"""Live chat REPL: python -m errand_agent.cli [--db runs/live.db] [--scenario timeout_after_success] [--seed 7]

Commands: /memory /remember k=v /correct k=v /forget k /confirm k, plus:
  /tick               poll for provider-side events (proactive follow-up)
  /provider-cancel ID simulate the provider cancelling booking ID
  /trace              print the path of the last trace file
  /quit
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from errand_agent.agent import Agent, Limits
from errand_agent.clock import Clock
from errand_agent.mocks.env import MockToolEnv, ScenarioConfig
from errand_agent.store import Store

SCENARIOS = {
    "default": {},
    "timeout_after_success": {"book_outcomes": ["timeout_booked"]},
    "slot_taken": {"book_outcomes": ["slot_taken"]},
    "price_drift": {"price_drift": {"*": 300}},
    "reschedule_conflict": {"reschedule_conflict_slots": {"2026-11-14T10:00"}},
}


def build(args) -> Agent:
    from errand_agent.models import XAIModel
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    env = MockToolEnv(seed=args.seed, clock=clock, config=ScenarioConfig(**SCENARIOS[args.scenario]))
    store = Store(args.db)
    return Agent(env=env, model=XAIModel(model=args.model), store=store, clock=clock, session_id=args.session,
                 limits=Limits(wall_clock_s=args.wall_clock), trace_dir=args.traces)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Errand Agent live chat")
    p.add_argument("--db", default="runs/live.db")
    p.add_argument("--traces", default="traces")
    p.add_argument("--session", default=f"live-{datetime.now():%Y%m%d-%H%M%S}")
    p.add_argument("--scenario", default="default", choices=sorted(SCENARIOS))
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--model", default="grok-4.7")
    p.add_argument("--wall-clock", type=float, default=90.0)
    args = p.parse_args(argv)
    agent = build(args)
    for r in agent.start():
        print(f"agent> {r.text}")
    print(f"Errand Agent (session {args.session}, scenario {args.scenario}). Traces in {args.traces}/{args.session}/")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/tick":
            replies = agent.tick() or []
            print("agent> " + ("\n".join(r.text for r in replies) or "(no new events)"))
            continue
        if text.startswith("/provider-cancel"):
            agent.env.provider_cancels(text.split()[1])
            print("(simulated: provider cancelled the booking; run /tick or keep chatting)")
            continue
        if text == "/trace":
            print(agent.tracer.path if agent.tracer else "(no trace yet)")
            continue
        r = agent.handle(text)
        print(f"agent> {r.text}")
        for n in agent.tick():
            print(f"agent (proactive)> {n.text}")


if __name__ == "__main__":
    main()
