# Real bugs, with regression tests

Bugs 1 to 3 happened during development. Bugs 4 to 10 came from a later code review, and bugs 11 to 17 from a second one. Every bug has a regression test in `tests/`, and the review bugs that change agent behaviour also have an eval scenario.

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

## 4. "Yes cancel it" did not cancel
- **What broke:** the cancel prompt says "Yes to cancel, or no to keep it", but the reply "yes cancel it" parsed as `other`. "Yes move it" and "book it" also failed.
- **Why:** `parse_decision()` treats "cancel" as a reject word and did not know "move", so the reply mixed a yes with a no.
- **Fix:** `parse_decision(text, action)` counts the pending action's own verb (book, cancel, move or reschedule) as a yes, unless the reply also has a reject word. With no pending cancel, "cancel" stays a no.
- **Regression tests:** `test_parse_decision_accepts_the_pending_action_verb`, eval `cancel_confirmed_with_action_verb`.

## 5. Reschedule accepted any slot
- **What broke:** `request_reschedule` opened an approval for a slot in the past or outside the provider's working hours.
- **Why:** the booking path only offers slots from a filtered search, but the reschedule path took the model's slot as given.
- **Fix:** `t_request_reschedule` refuses with `invalid_slot` unless the slot is an exact `YYYY-MM-DDTHH:00`, later than now, and in the provider's hours from `get_provider`.
- **Regression tests:** `test_regression_reschedule_rejects_past_and_off_hours_slots`, eval `reschedule_invalid_slot_refused`.

## 6. A provider cancellation silently dropped the user's pending approval
- **What broke:** while the user was deciding on a booking, `tick()` found a provider cancellation and opened a replacement approval. `Approvals.request()` superseded the user's approval and nobody told them.
- **Why:** the replacement path called `Approvals.request()` without checking for a pending approval.
- **Fix:** when an approval is pending, `tick()` tells the user about the cancellation, leaves their approval as it is, and does not hold a replacement.
- **Regression tests:** `test_regression_tick_keeps_the_users_pending_approval`, eval `proactive_cancellation_keeps_pending_approval`.

## 7. Earliest time "9:00" removed every slot
- **What broke:** with `earliest_time="9:00"`, search returned no candidates.
- **Why:** `apply_filters` compares `HH:MM` strings, and `"10:00" >= "9:00"` is false.
- **Fix:** `normalize_time()` turns "9", "9:00", "9am" and "10 a.m." into `HH:MM` in `Workflow.update()` and `Memory.remember()`, where the value enters.
- **Regression tests:** `test_regression_earliest_time_without_leading_zero_keeps_slots`, eval `earliest_time_without_leading_zero`.

## 8. Restart did not resolve an unverified booking
- **What broke:** after a book timeout with the status service down, the operation stayed `unknown`. A restart did not check it again.
- **Why:** `Executor.recover()` read only `pending` operations.
- **Fix:** `recover()` also reconciles `unknown` operations with `get_booking_status`.
- **Regression tests:** `test_restart_reconciles_unknown_operation_via_status`, eval `restart_reconciles_unverified_booking`.

## 9. Search and ask_user disagreed about remembered details
- **What broke:** `ask_user` refused to ask for area and budget because memory had them, but `search_providers` still refused with `missing_fields`.
- **Why:** search checked only the workflow request, while `ask_user` also read memory.
- **Fix:** search fills a missing area and budget from the same remembered values (`Router._known()`).
- **Regression tests:** `test_regression_search_uses_remembered_area_and_budget`, eval `search_uses_remembered_area_and_budget`.

## 10. The clock used the machine's time zone
- **What broke:** on a machine outside India, "now" was wrong for slots that are Asia/Kolkata times, so past slots could be offered or future ones dropped.
- **Why:** `Clock.now()` returned naive machine local time.
- **Fix:** `Clock.now()` reads Asia/Kolkata through `zoneinfo` and returns naive IST, which matches the slot strings.
- **Regression test:** `test_clock_uses_asia_kolkata_whatever_the_machine_zone`.

## 11. A booking claim got past the success-claim guard
- **What broke:** "No worries, you're booked with HSR Pipes for Tuesday 10:00." and "Done, approval was pre-granted." reached the user with no book call.
- **Why:** the guard skipped a whole sentence if any negation word was in it, and it knew only a few verbs.
- **Fix:** outcome text is rendered by the app, so model text is checked per clause. A claim word (booked, confirmed, cancelled, moved, scheduled, reserved, granted, approved, all set) counts as negated only when the negation or condition comes before it in the same clause. Questions and replies that name a booking in the ledger pass.
- **Regression tests:** `test_regression_claim_guard_blocks_claims_without_the_old_keywords`, `test_claim_guard_allows_a_reply_about_a_booking_in_the_ledger`, eval `prompt_injection_review`.

## 12. A provider cancellation could go unreported
- **What broke:** if `search_providers` raised inside `tick()`, the error escaped into the CLI loop and the user never heard about the cancellation.
- **Why:** `tick()` stored the cancellation as handled before it acted, and `_replace()` had no error handling.
- **Fix:** an error in `_replace()` still produces a notification. The cancellation is stored as handled only after the reply is saved.
- **Regression tests:** `test_regression_tick_tells_the_user_when_replacement_search_fails`, `test_regression_tick_marks_a_cancellation_handled_only_after_telling_the_user`, eval `proactive_cancellation_search_fails`.

## 13. A partial value was stored as an explicit preference
- **What broke:** the user wrote "under 1500" and the model saved `budget_max=150` as explicit.
- **Why:** `t_remember_preference` checked the value with a substring match.
- **Fix:** `said_in()` matches whole numbers and whole words after lowercasing, collapsing spaces and dropping thousands commas.
- **Regression tests:** `test_regression_explicit_memory_needs_the_whole_value_in_the_user_message`, eval `memory_partial_value_not_explicit`.

## 14. A forgotten preference still reached the model
- **What broke:** after `/forget preferred_area`, "Koramangala" was in the next model request.
- **Why:** the memory row was left out, but older chat messages that named the value were sent as they were.
- **Fix:** `ContextBuilder.build()` replaces forgotten values with `[forgotten]` in the history. The newest user message is kept, so the user can give the value again.
- **Regression tests:** `test_regression_forgotten_value_is_not_in_the_model_request`, eval `memory_forgetting` (checks the whole request).

## 15. One slow model call overran the wall-clock limit
- **What broke:** the limit was 45s, but a model call could run for 60s and then end as a model error with no `limit_hit`.
- **Why:** the limit was checked only between turns, and `XAIModel` used a fixed 60s HTTP timeout.
- **Fix:** each model call gets the time left in the budget as its timeout. A call that fails after the budget is spent returns the limit reply and records `limit_hit`.
- **Regression tests:** `test_regression_model_call_gets_the_remaining_wall_clock_budget`, eval `model_call_overruns_wall_clock`.

## 16. The model could loosen the user's constraints
- **What broke:** after it read an injected review, the model set `budget_max=0` and the search offered providers over the user's budget.
- **Why:** `update_request` accepted any value from the model.
- **Fix:** a higher or removed budget, a different time window, or an earlier start time is applied only when the current user message says so. Otherwise the field is refused and kept.
- **Regression tests:** `test_regression_model_cannot_loosen_the_users_constraints`, eval `injected_constraint_widening`.

## 17. Two processes produced the same booking ID
- **What broke:** each process rebuilt the mock with seed 7, so two bookings got `BK-4d3c1a` and the ledger kept one row.
- **Why:** booking IDs came from the seeded RNG.
- **Fix:** the booking ID is a hash of the idempotency key, which holds the approval's random ID.
- **Regression tests:** `test_regression_booking_ids_stay_unique_across_a_real_restart`, eval `booking_ids_unique_across_processes`.

### Smaller ones
- `Router.run` reported every `TypeError` as `bad_arguments`, including faults inside a tool. It now checks the arguments with `inspect.signature().bind()` first. Test: `test_regression_only_argument_binding_errors_are_bad_arguments`.
- The code named a real user ID and a fixed fallback area. The user ID is now `DEFAULT_USER_ID`, and `tick()` asks for an area when none is known. Test: `test_tick_without_a_known_area_asks_instead_of_guessing`.
- `Tracer.event(kind, **data)` collided with a `kind=` field in `run_end`, so every run raised `TypeError` before the first agent test went green. Fix: `kind` became positional-only and the field was renamed to `reply_kind`.
