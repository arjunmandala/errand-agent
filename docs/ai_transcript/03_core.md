All 55 tests passed: `.venv/bin/pytest -q` → `55 passed in 0.20s`.

Judgement calls:
- `"go ahead"` is a confirm idiom (the parametrized case) even though both tokens are filler; `"ahead"` alone stays `other`.
- A listed self-transition is a true no-op and does not append history.
- After a book timeout, a status other than `confirmed` or `not_found` is `unverified` and is not retried blindly. A later call will not replay a terminal operation.
- Cancel/reschedule recovery uses `list_bookings` because those idempotency keys are not on the booking-status index. If the change did not land, the operation is `not_applied` and a still-granted approval is invalidated so recovery does not auto-apply it.
