# Errand Agent polish + life layer

Date: 2026-11-06  
Status: draft for review  
Demo video: out of scope (Arjun records via `DEMO.md`)

## Goal

Close the remaining submit-critical doc gaps and make the project feel intentional rather than vibe-coded, without rewriting the engine. Add one hardening item that matches the brief’s “collect missing details.”

## Scope

In scope:

1. Humane system-prompt voice (no local stereotype, no fake familiarity).
2. Seed conversation rewrite (drop “Case NNN…” padding; keep ≥50 memories and ~220 messages).
3. Softer approval / CLI copy that still shows exact material details.
4. Design note cut to ~550 words in first-person trade-off voice; keep the timeout-after-success excerpt.
5. Thicken `AI_TRANSCRIPT.md` with an honest account of the 04/05 timeouts and agent-layer authorship.
6. Hard-gate `search_providers` until `time_window` and `budget_max` are set (or explicitly waived); one new eval.

Out of scope: Loom/demo recording, mock booking persistence across process death, background `tick()` polling, engine refactors.

## Voice

Rewrite the opening of `SYSTEM_PROMPT` in `errand_agent/tools.py` so the agent reads as a careful, brief human assistant for home-service booking. Keep every hard rule (missing-field discipline, approval via app, untrusted reviews, no success claims without verified outcomes, memory tools).

Do not add: regional slang, “Bengaluru ops” persona, emoji, or chatty filler. Tone target: short, concrete, humane.

## Seeds

Keep `_MEMORY_TABLE` in `errand_agent/memory_seed.py` (Maple Residency prefs, ≥50 rows after forget/correct).

Rewrite `_USER_LINES` / `_ASSIST_LINES` and `_fit()` so padded filler like “Case 003 stays a routine local errand” is gone. History must still stress the context budget (≥80 chars per message is fine via natural detail, not boilerplate). `seed_conversation(..., n=220)` stays. Context-budget eval must still pass.

## Copy

`render_approval()` in `agent.py` keeps exact provider, slot, price, hold expiry, and booking ids. Soften only the wrapper phrasing (e.g. “Want me to book …?” / “Cancel …?” / “Move …?”) so it doesn’t read like a form letter. Touch a small set of CLI / limit / error strings the same way. Do not change `parse_decision` semantics.

## Docs

**Design note (`docs/DESIGN_NOTE.md`).** Target ~550 words (max ~600). Lead with the single-agent vs planner/executor trade-off, then state machine, approval digests, idempotency/recovery, memory+budget, proactive `tick()`, observability. Keep the abridged live timeout-after-success excerpt. Drop repeated mechanism detail and the mutation anecdote if needed for length. No employer name in public docs.

**AI transcript (`AI_TRANSCRIPT.md`).** Keep the grok-agent call table. Add a short section stating that calls 04/05 timed out / stalled and the agent layer (`agent.py`, `router.py`, tools/context/memory/sanitize/trace/models/cli, evals) was written by the orchestrator against the failing tests; point at existing prompt files under `docs/ai_transcript/`. Do not invent turn-by-turn logs that don’t exist.

## Hardening: budget / time gate

Extend `Workflow.REQUIRED` to include `time_window` and `budget_max` (in addition to `service`, `area`, `date_range`).

Waivers (explicit, not inferred):

- `time_window="any"` counts as set.
- `budget_max=0` means “no budget cap”; app-side filters skip the budget check when value is 0.

`Router.t_search_providers` already refuses when `wf.missing()` is non-empty; keep that path. Memory may pre-fill these via known prefs the same way as today.

Update any eval/scripted paths that search without those fields. Add one offline scenario: search attempted while budget (or time window) missing → `missing_fields`; after `update_request` sets it → search proceeds.

## Testing / verification

- `.venv/bin/pytest -q` green (including the new gate scenario).
- `python -m evals.run` regenerates the eval table; all scenarios pass.
- `wc -w docs/DESIGN_NOTE.md` ≈ 500–600.
- Manual skim: system prompt opening, one approval string, a few seeded history lines, AI transcript honesty section.
- No employer name in README / design note / commit subjects for this work.

## Non-goals reminder

Do not add background tick, persist the in-process mock, or change the 70/15/15 / approval / idempotency architecture.
