# Errand Agent: design note

I kept one agent. The model reads, compares, and requests. Python owns the state machine, approvals, side effects, retries, recovery, memory filtering, and the token budget. I looked at a planner/executor split and rejected it. The parts that can hurt someone are not reasoning problems. A second model would only add another trust boundary, and the brief’s failures (double booking, a price change after a yes, a timeout that still landed) are fixed by a ledger and a digest, not by more planning.

The lifecycle is collecting, searching, proposing, awaiting approval, executing, verifying, and done, plus failed and recovering. `workflow.py` whitelists transitions. `Agent.goto()` walks the shortest legal path. Approvals, the operation log, the booking ledger, and memories sit in SQLite, so a restart resumes mid-flow.

Search waits for service, area, date range, time window, and budget. `time_window="any"` is an explicit waiver. `budget_max=0` means no cap. If the request has no area or budget, search fills them from remembered preferences, which `ask_user` also treats as known. Only the user's own message can loosen a stated budget, time window, or earliest time.

The model has no book, cancel, reschedule, or hold tool. `request_booking` freezes action, provider, slot, and quoted price into an approval with a SHA-256 digest. Only a user message that `parse_decision()` treats as a plain yes grants it. “Yes but 11am”, a fake system line, and anything from a tool do not. The executor runs only with a granted, unexpired, unconsumed approval whose digest matches. A changed detail or a new price invalidates that approval. A repeated yes cannot replay a consumed approval. Outcome text is rendered by the app. A model reply that claims a booking, cancellation, or move is blocked unless it names a booking in the ledger.

The idempotency key is `idem-<approval_id>`, written as pending before book, cancel, or reschedule. If book times out, `get_booking_status(key)` decides. Confirmed means success and no second book. Not found means retry with the same key, at most three times. If status is down, the reply is unverified and there is no blind retry. An expired hold is re-held; a new price invalidates the approval. On startup, `Agent.start()` resolves every pending operation. A landed booking is confirmed with no second call. If nothing landed, nothing is auto-booked.

Memories carry kind, confirmed, scope, and status. A correction supersedes the old row and keeps it for audit. `remember_preference` is explicit only when the value is in the current user message; otherwise it is inferred and unconfirmed, so tool text cannot plant a preference. Only active explicit or confirmed facts in scope reach the model, and forgotten values are cut from the chat history it is sent. The default budget is 4,000 tokens. Older tool results compact first. The estimator calibrates against provider-reported input tokens, because a chars/4 guess under-counted by about 1.9×.

`tick()` is called by the CLI, not a background loop. A provider cancellation of an upcoming booking is announced. The app searches the original day plus three days inside known preferences, excluding that provider, holds the closest slot, and opens a fresh approval. Past bookings are recorded quietly. If nothing fits, nothing is held.

Phone numbers and emails are redacted in the run log. Instruction-like review text is replaced before the model sees it.

**Trace excerpt** (live grok-4.7 run, `docs/traces/e2e-timeout-after-success.jsonl`, abridged):
```
request_booking PLB-03 2026-11-07T08:00 (model)  → hold_slot H-a5cd68 ₹900 (app) → approval AP-793e… digest 08ad9a…
prompt, as the app now words it (this run said "Please confirm: …"): "Want me to book TapMasters (PLB-03) … at ₹900?"
user "yes" → decision_parsed=confirm → granted → awaiting_approval→executing
book(H-a5cd68, idem-AP-793e…)    error: ToolTimeout "timed out after success"   ← ambiguous outcome
get_booking_status(idem-AP-793e…) → confirmed BK-4d3c1a                          ← source of truth, no re-book
executing→verifying→done; reply "Booked … (the booking call timed out, so I verified it …)"
user "yes" again → no pending approval → model: "Already booked …" (book calls total: 1)
```
