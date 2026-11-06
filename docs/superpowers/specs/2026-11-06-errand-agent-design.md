# Errand Agent: Design Spec

Date: 2026-11-06. Status: approved direction (Arjun: Python, single agent, explicit state machine).
Source of truth for requirements: `docs/Engineering_Assignment.pdf`.

## 1. Goal
A local, chat-based agent that arranges home-service errands (plumber, AC servicing, electrician, ...)
against mocked provider tools. A real LLM understands requests and chooses tool calls; every
consequential side effect (book / cancel / reschedule) is gated by application code.

## 2. Shape (single agent, deterministic shell around the model)
```
user msg ──► Agent.handle()
              ├─ pending approval? ── DecisionParser (app code) ── confirm ─► Executor (book/cancel/reschedule)
              │                                                 └ reject ──► clear approval
              └─ model loop (bounded): ContextBuilder ► Model ► ToolRouter ► (safe tools | request_* tools)
                                                                              └─ request_* creates PendingApproval
```
One agent is enough: the hard parts (approval, idempotency, recovery) are deterministic code, not
reasoning. A second agent would add a trust boundary to reason about without solving a problem we have.

Why the model never gets `book`/`cancel`/`reschedule`: the model can only *request* an action
(`request_booking`, `request_cancel`, `request_reschedule`). The app holds the slot, freezes the exact
material details (action, provider, slot, quoted price, booking id) into an approval record, renders the
approval prompt itself, and executes only after a *user* message that the app's DecisionParser
classifies as an unambiguous confirmation. Prompt-injected text or model output can't produce that.

## 3. Workflow state machine
States: `collecting → searching → proposing → awaiting_approval → executing → verifying → done`
plus `failed`, and `recovering` (startup/after timeout). Transitions are a whitelist; an illegal
transition raises and is traced. State is persisted per session in SQLite, so a restart resumes it.

## 4. Approval enforcement
- `approvals` table: id, session, action (book|cancel|reschedule), canonical details JSON, digest
  (sha256 of canonical JSON), status (pending|granted|consumed|invalidated|rejected), expires_at.
- The Executor calls a side-effect tool only with a *granted* approval whose digest equals the digest of
  the action it is about to perform. Re-hold with a different quoted price, a different slot or
  provider → new digest → old approval invalidated → fresh approval prompt.
- Consumed approvals can't be reused, so a repeated "yes" does nothing new.

## 5. Idempotency and recovery
- Idempotency key = `idem-<approval_id>`; it is written to the `operations` table (status `pending`)
  *before* the side-effect call (write-ahead). Same approval → same key on retry and after restart.
- Mock `book`/`cancel`/`reschedule` honour idempotency keys (same key → same result).
- `book` timeout → `get_booking_status(idempotency_key)` is the source of truth: confirmed → success;
  not found → retry `book` with the same key (bounded); still unknown → tell the user it is unverified.
- On startup `Agent.recover()` resolves every `pending` operation via `get_booking_status`.
- `cancel` 503 → bounded retry with backoff and the same key.
- Never claim success until the status is verified.

## 6. Memory and context
- `memories`: key, value, kind (explicit|inferred), confirmed, scope (global or a service), status
  (active|forgotten|superseded), source, timestamps. Inspect/correct/forget via tools and `/memory`.
- Correction supersedes (old row kept for audit, never sent). Forget marks `forgotten` (never sent).
- ContextBuilder: fixed token budget (default 1,800 est. tokens, chars/4). Priority: system prompt →
  workflow state → relevant active memories (explicit, or inferred+confirmed; scope global or current
  service) → newest messages until the budget is hit. Each turn logs which memory ids and how many
  messages/tokens were sent.
- Seed: ≥50 memories (incl. forgotten, superseded, unconfirmed inferred, other-service) and a ≥200 message
  prior conversation.

## 7. Bounded execution
Per user message: max model turns (6), max tool calls (12), wall clock (45 s, injectable clock), max
side-effect retries (3). Every exit path returns a user-facing message; exceptions in tools become
structured errors; limit exhaustion leaves state consistent (no half-granted approvals).

## 8. Untrusted tool output
Free text from tools (reviews, notes) is wrapped as data (`{"untrusted_text": ...}`), injection-like
phrases are flagged and neutralised, and permissions never depend on it (see §2). Evals use a scripted
model that *obeys* the injection to prove the app still blocks it.

## 9. Proactive follow-up
`Agent.tick()` polls `list_bookings` against the local ledger. A provider-side cancellation of an
upcoming booking → notify the user, search a replacement inside known preferences (service, area,
earliest time, budget, same window), hold it, and open a fresh approval. Never rebooks without one.
Past bookings are recorded but not notified.

## 10. Observability
JSONL trace per run under `traces/`: run_start, context (memory ids, tokens), model_call (latency,
tokens, est. cost), tool_call (name, args, sanitized result, latency, error), state_transition,
approval events, run_end with outcome. Phone numbers/emails redacted; long text truncated.

## 11. Models
- `XAIModel`: xAI chat-completions with function calling (`XAI_API_KEY`), usage → cost estimate.
- `ScriptedModel`: deterministic queue of responses for offline evals; no network in tests.

## 12. Evals
≥20 scenarios with simulated users, each asserting invariants on the mock environment and the DB
(e.g., bookings in mock == 1, no side effect without a granted approval with matching digest), and
producing `eval_results.md` / `eval_table.csv` from the run.

## Non-goals
Real providers/payments, web UI, multi-user auth.
