# Three real bugs hit while building

Every bug below happened during development and has a regression test in `tests/test_agent.py`.

## 1. A slot that had already started was offered, booked, and its cancellation went unreported
- **What broke:** the proactive-cancellation test failed. `tick()` returned nothing after the provider cancelled a booking.
- **Why:** with the clock at Tue 08:00, the mock returned today's 08:00 slot and the agent booked it. When the provider cancelled, `tick()` treated the booking as "in the past" (`slot <= now`) and stayed silent. So two problems combined: the search offered a slot that had already started, and the notify decision then made a silent bad choice.
- **Fix:** `Router.apply_filters` adds a `not_before=now` constraint. This applies to both normal search and replacement search.
- **Regression test:** `test_regression_search_never_offers_slots_that_already_started`

## 2. The token budget was enforced on the wrong thing
- **What broke:** the first live grok-4.7 run logged context `tokens ≤ 1,800`, but xAI reported 2,652–4,027 input tokens per call.
- **Why:** the builder counted only system, memories and history. The tool schemas (~956 estimated tokens) and this turn's tool results (search results with many slots) were added on top without being counted. On top of that, chars/4 under-counts the provider tokenizer by about 1.9×.
- **Fix:** the budget now covers the whole request. `ContextBuilder.fit()` compacts older tool results, then drops the oldest history, then truncates the newest result. The estimator self-calibrates against reported `input_tokens`, and each turn logs `request_tokens` and `scale`.
- **Regression tests:** `test_regression_whole_model_request_fits_budget_including_tools_and_scratch`. It fails when compaction is disabled, with `[2031 … 3294] > 2400`. Also `test_budget_self_calibrates_to_provider_reported_input_tokens`.

## 3. The model described a booking it never requested, so the user's "yes" confirmed nothing
- **What broke:** in a live run the user said "book the best rated one". The model replied *"Proposing TapMasters … the app will show the exact details for you to confirm"* as plain text and never called `request_booking`. The user said "yes", but no approval existed. The model then made up an expired hold. The success-claim guard also fired on that reply because it contained "nothing was booked", and swapped the honest negative answer for a canned message.
- **Why:** nothing checked for an action that was described but not taken. The context never said explicitly that no approval was pending. The guard matched keywords without handling negation.
- **Fix:** if the reply narrates an action without a `request_*` call, the agent sends one bounded nudge (traced as `nudge`). The context now states `pending_approval: none`. `claims_success()` works per sentence and ignores negated sentences.
- **Regression tests:** `test_regression_narrated_proposal_without_tool_call_is_nudged` and `test_regression_claim_guard_allows_negative_statements`.

### Smaller one
- `Tracer.event(kind, **data)` collided with a `kind=` field in `run_end`, so every run raised `TypeError` before the first agent test went green. Fix: `kind` became positional-only and the field was renamed to `reply_kind`.
