# AI coding assistants used

Two assistants were used, and both are listed here.

1. **Grok Bot (orchestrator).** It drove the Superpowers workflow: design spec, plan, tests first, then implementation and verification. It wrote all the test files, `agent.py`, `router.py`, `tools.py`, `context.py`, `memory.py`, `sanitize.py`, `trace.py`, `models.py`, `cli.py`, the eval harness and scenarios, and the docs. It also ran the live sessions, found bugs 2 and 3 from live traces, and fixed them test-first.
2. **grok-agent CLI** (xAI grok-4.7, `--effort medium`/`low`). Used for review and for code generation against tests that were already failing. Every prompt and output is in [docs/ai_transcript/](docs/ai_transcript/).

| # | Call | Purpose | Result | Usage (from CLI) |
|---|---|---|---|---|
| 1 | `01_spec_review` | Review the design spec against the brief | Found gaps that were then built in: memory writes must not come from tool text, an unreachable status service means "unverified", restart while awaiting approval must not book, and the next state after slot_taken/hold_expired needed defining | 8.8k tokens, $0.012 |
| 2 | `02_prompt` → `02_mocks` | Implement `clock.py` and `mocks/` against 16 failing contract tests | 16/16 green; reviewed by hand | 142k tokens, $0.090 |
| 3 | `03_prompt` → `03_core` | Implement workflow, store, approval and executor against 39 failing tests | 55/55 green. Judgement calls are recorded in `03_core.md` | 300k tokens, $0.150 |
| 4 | `04_prompt` | Implement the agent layer in a single call | Interrupted after about 11 minutes with no output. The prompt was too large for one tool-loop step | none recorded |
| 5 | `05_partA_prompt` | Retry with the work split in two | Killed after about 24 minutes stuck in one generation step. Grok Bot wrote these modules directly instead | none recorded |
| 6 | `07_seed` | Generate `memory_seed.py`: 56+ seeded memories and a 220-message history | Used as generated | low effort |

Live model spend while testing the agent: the recorded e2e session used 20,652 input / 1,096 output tokens, $0.0125 (from `docs/traces/e2e-timeout-after-success.jsonl`).
