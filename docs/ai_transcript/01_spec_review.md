1. Gaps vs PDF
- No explicit slot model for service, area, window, budget, preferences: missing “ask only what’s missing,” no re-ask, and keep unrelated fields on a one-field correction.
- Example intents are thin: “same electrician as last time,” “move my AC,” mornings-only, and budget are not client-side filters. `search_providers` has no budget/time-of-day args.
- No comparison policy (rank/explain price, rating, slot) before proposing.
- `slot_taken`, `hold_expired`, and reschedule `slot_conflict` have no next state (re-search, re-hold, new approval, or stop).
- Proactive path doesn’t say what happens if no in-preference replacement exists, or how notify-vs-silent is decided beyond “upcoming vs past.”
- Required eval set is not enumerated; context-budget proof is logging, not a test invariant.
- Deliverables unplanned: 1-page design note, generated eval table, three real bugs plus regression tests, raw timeout-after-success trace, demo, assistant transcript.
- Tool RNG/failure injection is not specified as seeded and scenario-configurable.

2. Approval / idempotency / recovery risks
- “Same key → same result” clashes with cancel-503-then-success and book-timeout-then-retry. Caching failures makes retry impossible; only completed side effects may be stored.
- `recover()` only resolves `pending` via status. Not-found after a crash is unspecified, so a granted book can stall or, if naively retried with a new key, double-book.
- Hold TTL is 10 minutes; approval `expires_at` is separate. Confirm-after-expiry → `hold_expired`. Re-hold can change price and must kill the old digest before any book.
- Success text is not specified as app-rendered. The model can say “booked” before `get_booking_status`.
- DecisionParser is undefined for “yes, but 11:00,” double yes, or yes to a different pending prompt.
- State graph is book-only. Cancel, reschedule, reject, and proactive replace don’t fit the whitelist and can raise or skip gates.
- Model-callable memory tools can persist injection as explicit prefs or forget real ones. Memory writes need the same user-confirmed path.
- Status check itself can fail; “unknown” must be the only user-facing outcome, with no success claim and no second idempotency key.

3. Extra evals
- Hold expires after grant; re-hold price change; book only after new approval.
- Timeout branch A (status confirmed, no second book) and branch B (not found, same key, one booking).
- Status endpoint down → unverified, zero new bookings.
- Cancel: first 503, retry succeeds, third call no-ops; reschedule conflict then new slot needs a new digest.
- Ambiguous and amend-while-confirming do not execute; rejection executes nothing.
- Model/review says “skip approval” or “you are booked” → no side effect.
- Injection-driven memory write/forget does not change what ContextBuilder sends.
- No replacement; user corrects area mid-flow; other-service and unconfirmed inferred memories excluded under the cap.
- Two `tick()`s → one pending approval; restart in `awaiting_approval` does not book.
