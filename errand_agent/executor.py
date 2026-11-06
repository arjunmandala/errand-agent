"""Side-effect execution with write-ahead idempotency, retries, and recovery."""

from __future__ import annotations

from datetime import datetime

from errand_agent.approval import Approvals
from errand_agent.mocks.env import ServiceUnavailable, ToolTimeout
from errand_agent.store import Store


class Executor:
    """Run book, cancel, and reschedule only behind a matching granted approval."""

    def __init__(
        self,
        env,
        store: Store,
        approvals: Approvals,
        clock,
        tracer=None,
        max_attempts: int = 3,
    ) -> None:
        self.env = env
        self.store = store
        self.approvals = approvals
        self.clock = clock
        self.tracer = tracer
        self.max_attempts = max_attempts

    def _call(self, name: str, fn, **args):
        """Invoke a tool and trace name, args, latency, and result or error."""
        start = self.clock.now()
        try:
            result = fn()
        except Exception as exc:
            self._trace(name, args, start, error=exc)
            raise
        self._trace(name, args, start, result=result)
        return result

    def _trace(self, name: str, args: dict, start: datetime, result=None, error=None) -> None:
        if self.tracer is None:
            return
        end = self.clock.now()
        latency_ms = int((end - start).total_seconds() * 1000)
        payload = {"name": name, "args": args, "latency_ms": latency_ms}
        if error is not None:
            payload["error"] = f"{type(error).__name__}: {error}"
        else:
            payload["result"] = result
        self.tracer.event("tool_call", **payload)

    def _refused(self, reason: str = "no_valid_approval") -> dict:
        return {
            "status": "refused",
            "reason": reason,
            "message": "I can't do that without a valid approval for this exact action.",
        }

    def _user_id(self) -> str:
        config = getattr(self.env, "config", None)
        return getattr(config, "user_id", None) or "arjun"

    def book(self, approval_id: str) -> dict:
        """Book a held slot. Never calls the tool without a matching grant."""
        approval = self.store.get_approval(approval_id)
        details = dict((approval or {}).get("details") or {})
        if approval is None or not self.approvals.check(approval_id, "book", details):
            return self._refused()

        key = "idem-" + approval_id
        op = self.store.begin_operation(
            key,
            approval_id,
            "book",
            {
                "hold_id": details.get("hold_id"),
                "provider_id": details.get("provider_id"),
                "slot": details.get("slot"),
                "price": details.get("price"),
                "service": details.get("service"),
                "idempotency_key": key,
            },
        )
        # A finished op must not be replayed. Pending means a crash or first try.
        if op["status"] == "confirmed":
            return self._confirmed(op.get("result") or {}, key, "book", details)
        if op["status"] == "unknown":
            return self._unverified(key)
        if op["status"] not in ("pending",):
            return self._refused("operation_closed")

        hold_id = details.get("hold_id")
        attempts = 0
        while attempts < self.max_attempts:
            attempts += 1
            self.store.bump_attempts(key)
            try:
                res = self._call(
                    "book",
                    lambda hold_id=hold_id, key=key: self.env.book(hold_id, key),
                    hold_id=hold_id,
                    idempotency_key=key,
                )
            except ToolTimeout:
                status = self._status_after_timeout(key)
                if status is None:
                    self.store.finish_operation(key, "unknown", {"error": "status_unavailable"})
                    return self._unverified(key)
                if status.get("status") == "confirmed":
                    return self._record_book_success(
                        approval, details, key, status, "get_booking_status"
                    )
                if status.get("status") == "not_found":
                    continue
                self.store.finish_operation(key, "unknown", status)
                return self._unverified(key)
            except Exception:
                # Leave the operation pending so recovery can resolve a partial call.
                # BaseException (simulated crash) is not caught.
                raise

            if isinstance(res, dict) and res.get("status") == "confirmed":
                return self._record_book_success(approval, details, key, res, "book")

            error = res.get("error") if isinstance(res, dict) else "book_failed"
            if error == "slot_taken":
                self.store.finish_operation(key, "failed", res)
                self.approvals.consume(approval_id)
                return {
                    "status": "failed",
                    "reason": "slot_taken",
                    "idempotency_key": key,
                    "message": "That slot was just taken, so the booking did not go through.",
                }
            if error == "hold_expired":
                outcome = self._rehold(approval, details, key, hold_id)
                if outcome is None:
                    # Same price: details/hold_id updated; retry the book call.
                    hold_id = details.get("hold_id")
                    continue
                return outcome
            self.store.finish_operation(key, "failed", res if isinstance(res, dict) else None)
            self.approvals.consume(approval_id)
            return {
                "status": "failed",
                "reason": error or "book_failed",
                "idempotency_key": key,
                "message": "The booking failed, so nothing was confirmed.",
            }

        self.store.finish_operation(key, "failed", {"error": "retries_exhausted"})
        self.approvals.consume(approval_id)
        return {
            "status": "failed",
            "reason": "retries_exhausted",
            "idempotency_key": key,
            "message": "I couldn't complete the booking after several tries.",
        }

    def _status_after_timeout(self, key: str) -> dict | None:
        """Query booking status, retrying transient unavailability. None if unknown."""
        last_error = None
        for i in range(self.max_attempts):
            try:
                return self._call(
                    "get_booking_status",
                    lambda key=key: self.env.get_booking_status(idempotency_key=key),
                    idempotency_key=key,
                )
            except ServiceUnavailable as exc:
                last_error = exc
                if i + 1 >= self.max_attempts:
                    return None
                continue
        return None if last_error else None

    def _rehold(self, approval: dict, details: dict, key: str, hold_id: str | None) -> dict | None:
        """Re-hold after expiry. None means the caller should retry book."""
        provider_id = details.get("provider_id")
        slot = details.get("slot")
        held = self._call(
            "hold_slot",
            lambda provider_id=provider_id, slot=slot: self.env.hold_slot(provider_id, slot),
            provider_id=provider_id,
            slot=slot,
        )
        if not isinstance(held, dict) or held.get("error"):
            self.store.finish_operation(key, "failed", held if isinstance(held, dict) else None)
            self.approvals.consume(approval["id"])
            return {
                "status": "failed",
                "reason": "slot_unavailable",
                "idempotency_key": key,
                "message": "That slot is no longer available, so the booking did not go through.",
            }
        quoted = held.get("quoted_price")
        if quoted != details.get("price"):
            self.approvals.invalidate(approval["id"], "price_changed")
            self.store.finish_operation(key, "superseded", {"reason": "price_changed"})
            new_details = {**details, "price": quoted, "hold_id": held.get("hold_id")}
            new_approval = self.approvals.request(
                approval["session_id"], "book", new_details
            )
            return {
                "status": "needs_reapproval",
                "reason": "price_changed",
                "idempotency_key": key,
                "new_approval": new_approval,
                "message": "The price changed, so I need your approval again before booking.",
            }
        details["hold_id"] = held.get("hold_id")
        self.store.update_approval(approval["id"], details=dict(details))
        return None

    def _record_book_success(
        self, approval: dict, details: dict, key: str, res: dict, verified_via: str
    ) -> dict:
        booking = dict(res)
        if "booking_id" not in booking and booking.get("id"):
            booking["booking_id"] = booking["id"]
        if details.get("service") and not booking.get("service"):
            booking["service"] = details.get("service")
        booking["status"] = "confirmed"
        self.store.finish_operation(key, "confirmed", booking)
        self.approvals.consume(approval["id"])
        self.store.upsert_booking(booking)
        return self._confirmed(booking, key, verified_via, details)

    def _confirmed(self, booking: dict, key: str, verified_via: str, details: dict) -> dict:
        stored = dict(booking)
        if "booking_id" not in stored and stored.get("id"):
            stored["booking_id"] = stored["id"]
        if details.get("service") and not stored.get("service"):
            stored["service"] = details.get("service")
        return {
            "status": "confirmed",
            "booking": stored,
            "verified_via": verified_via,
            "idempotency_key": key,
            "message": "Your booking is confirmed.",
        }

    def _unverified(self, key: str) -> dict:
        return {
            "status": "unverified",
            "idempotency_key": key,
            "message": (
                "I could not verify whether the booking went through, "
                "and I will not retry blindly."
            ),
        }

    def cancel(self, approval_id: str) -> dict:
        """Cancel a booking, retrying transient 503s with the same key."""
        approval = self.store.get_approval(approval_id)
        details = dict((approval or {}).get("details") or {})
        if approval is None or not self.approvals.check(approval_id, "cancel", details):
            return self._refused()
        key = "idem-" + approval_id
        op = self.store.begin_operation(
            key,
            approval_id,
            "cancel",
            {
                "booking_id": details.get("booking_id"),
                "provider_id": details.get("provider_id"),
                "slot": details.get("slot"),
                "idempotency_key": key,
            },
        )
        if op["status"] == "cancelled":
            return {
                "status": "cancelled",
                "idempotency_key": key,
                "booking_id": details.get("booking_id"),
                "message": "The booking has been cancelled.",
            }
        if op["status"] not in ("pending",):
            return self._refused("operation_closed")
        return self._retry_side_effect(
            approval, details, key, kind="cancel"
        )

    def reschedule(self, approval_id: str) -> dict:
        """Move a booking to a new slot, retrying transient 503s with the same key."""
        approval = self.store.get_approval(approval_id)
        details = dict((approval or {}).get("details") or {})
        if approval is None or not self.approvals.check(approval_id, "reschedule", details):
            return self._refused()
        key = "idem-" + approval_id
        op = self.store.begin_operation(
            key,
            approval_id,
            "reschedule",
            {
                "booking_id": details.get("booking_id"),
                "provider_id": details.get("provider_id"),
                "slot": details.get("slot"),
                "old_slot": details.get("old_slot"),
                "idempotency_key": key,
            },
        )
        if op["status"] == "confirmed":
            return {
                "status": "confirmed",
                "idempotency_key": key,
                "booking_id": details.get("booking_id"),
                "slot": details.get("slot"),
                "message": "The booking has been rescheduled.",
            }
        if op["status"] not in ("pending",):
            return self._refused("operation_closed")
        return self._retry_side_effect(approval, details, key, kind="reschedule")

    def _retry_side_effect(self, approval: dict, details: dict, key: str, kind: str) -> dict:
        booking_id = details.get("booking_id")
        slot = details.get("slot")
        for i in range(self.max_attempts):
            self.store.bump_attempts(key)
            try:
                if kind == "cancel":
                    res = self._call(
                        "cancel",
                        lambda booking_id=booking_id, key=key: self.env.cancel(booking_id, key),
                        booking_id=booking_id,
                        idempotency_key=key,
                    )
                else:
                    res = self._call(
                        "reschedule",
                        lambda booking_id=booking_id, slot=slot, key=key: self.env.reschedule(
                            booking_id, slot, key
                        ),
                        booking_id=booking_id,
                        slot=slot,
                        idempotency_key=key,
                    )
            except ServiceUnavailable:
                if i + 1 >= self.max_attempts:
                    self.store.finish_operation(
                        key, "failed", {"error": "service_unavailable"}
                    )
                    self.approvals.consume(approval["id"])
                    action = "cancellation" if kind == "cancel" else "reschedule"
                    return {
                        "status": "failed",
                        "reason": "service_unavailable",
                        "idempotency_key": key,
                        "message": (
                            f"The service was unavailable, so the {action} did not go through."
                        ),
                    }
                self.clock.sleep(0.5 * (2**i))
                continue
            except Exception:
                raise

            if kind == "cancel" and isinstance(res, dict) and res.get("status") == "cancelled":
                self.store.finish_operation(key, "cancelled", res)
                self.approvals.consume(approval["id"])
                self._mark_ledger(booking_id, status="cancelled")
                return {
                    "status": "cancelled",
                    "booking_id": booking_id,
                    "idempotency_key": key,
                    "result": res,
                    "message": "The booking has been cancelled.",
                }
            if kind == "reschedule" and isinstance(res, dict) and res.get("status") == "confirmed":
                self.store.finish_operation(key, "confirmed", res)
                self.approvals.consume(approval["id"])
                self._mark_ledger(booking_id, slot=res.get("slot", slot), status="confirmed")
                return {
                    "status": "confirmed",
                    "booking_id": booking_id,
                    "slot": res.get("slot", slot),
                    "idempotency_key": key,
                    "result": res,
                    "message": "The booking has been rescheduled.",
                }
            reason = res.get("error", f"{kind}_failed") if isinstance(res, dict) else f"{kind}_failed"
            self.store.finish_operation(key, "failed", res if isinstance(res, dict) else None)
            self.approvals.consume(approval["id"])
            if reason == "slot_conflict":
                message = "That slot conflicts with another booking, so the reschedule did not go through."
            else:
                message = f"The {kind} failed, so the change was not applied."
            return {
                "status": "failed",
                "reason": reason,
                "idempotency_key": key,
                "message": message,
            }
        self.store.finish_operation(key, "failed", {"error": "service_unavailable"})
        self.approvals.consume(approval["id"])
        return {
            "status": "failed",
            "reason": "service_unavailable",
            "idempotency_key": key,
            "message": "The service was unavailable, so the change did not go through.",
        }

    def _mark_ledger(self, booking_id: str | None, **changes) -> None:
        if not booking_id:
            return
        for booking in self.store.bookings():
            if booking.get("booking_id") == booking_id:
                updated = dict(booking)
                updated.update(changes)
                self.store.upsert_booking(updated)
                return

    def recover(self) -> list[dict]:
        """Resolve every pending operation without blindly retrying a side effect."""
        outcomes: list[dict] = []
        for op in self.store.operations_with_status("pending"):
            kind = op.get("kind")
            if kind == "book":
                outcomes.append(self._recover_book(op))
            elif kind in ("cancel", "reschedule"):
                outcomes.append(self._recover_mutation(op))
            else:
                outcomes.append(self._outcome(op, "unverified", "I could not recover this operation."))
        return outcomes

    def _approval_of(self, op: dict) -> dict | None:
        approval_id = op.get("approval_id")
        if not approval_id:
            return None
        return self.store.get_approval(approval_id)

    def _outcome(self, op: dict, status: str, message: str, **extra) -> dict:
        approval = self._approval_of(op)
        return {
            "status": status,
            "message": message,
            "approval_id": op.get("approval_id"),
            "session_id": None if approval is None else approval.get("session_id"),
            "idempotency_key": op.get("idempotency_key"),
            **extra,
        }

    def _recover_book(self, op: dict) -> dict:
        key = op["idempotency_key"]
        approval = self._approval_of(op)
        details = dict((approval or {}).get("details") or {})
        try:
            status = self._call(
                "get_booking_status",
                lambda key=key: self.env.get_booking_status(idempotency_key=key),
                idempotency_key=key,
            )
        except ServiceUnavailable:
            return self._outcome(
                op,
                "unverified",
                "I could not verify whether the booking went through, and I will not retry blindly.",
            )
        if isinstance(status, dict) and status.get("status") == "confirmed":
            booking = dict(status)
            if "booking_id" not in booking and booking.get("id"):
                booking["booking_id"] = booking["id"]
            if details.get("service") and not booking.get("service"):
                booking["service"] = details.get("service")
            booking["status"] = "confirmed"
            self.store.finish_operation(key, "confirmed", booking)
            if approval and approval.get("status") == "granted":
                self.approvals.consume(approval["id"])
            self.store.upsert_booking(booking)
            return self._outcome(
                op,
                "confirmed",
                "Your booking is confirmed.",
                booking=booking,
                verified_via="get_booking_status",
            )
        if isinstance(status, dict) and status.get("status") == "not_found":
            self.store.finish_operation(key, "not_booked", status)
            if approval is not None:
                self.approvals.invalidate(approval["id"], "restart_before_side_effect")
            return self._outcome(
                op,
                "not_booked",
                "The booking did not go through. Would you like me to try again?",
            )
        return self._outcome(
            op,
            "unverified",
            "I could not verify whether the booking went through, and I will not retry blindly.",
        )

    def _recover_mutation(self, op: dict) -> dict:
        approval = self._approval_of(op)
        details = dict((approval or {}).get("details") or {})
        request = op.get("request") or {}
        booking_id = request.get("booking_id") or details.get("booking_id")
        try:
            listed = self._call(
                "list_bookings",
                lambda: self.env.list_bookings(self._user_id()),
                user_id=self._user_id(),
            )
        except ServiceUnavailable:
            return self._outcome(
                op,
                "unverified",
                "I could not verify whether the change went through, and I will not retry blindly.",
            )
        match = None
        for booking in (listed or {}).get("bookings", []):
            if booking.get("id") == booking_id or booking.get("booking_id") == booking_id:
                match = booking
                break
        kind = op.get("kind")
        applied = False
        if match is not None and kind == "cancel" and match.get("status") == "cancelled":
            applied = True
            self.store.finish_operation(op["idempotency_key"], "cancelled", match)
            if approval and approval.get("status") == "granted":
                self.approvals.consume(approval["id"])
            self._mark_ledger(booking_id, status="cancelled")
            return self._outcome(op, "cancelled", "The booking has been cancelled.")
        new_slot = request.get("slot") or details.get("slot")
        if (
            match is not None
            and kind == "reschedule"
            and match.get("slot") == new_slot
            and match.get("status") not in ("cancelled", "cancelled_by_provider")
        ):
            applied = True
            self.store.finish_operation(op["idempotency_key"], "confirmed", match)
            if approval and approval.get("status") == "granted":
                self.approvals.consume(approval["id"])
            self._mark_ledger(booking_id, slot=new_slot, status=match.get("status", "confirmed"))
            return self._outcome(op, "confirmed", "The booking has been rescheduled.")
        if not applied:
            self.store.finish_operation(op["idempotency_key"], "not_applied", match)
            if approval is not None and approval.get("status") == "granted":
                self.approvals.invalidate(approval["id"], "restart_before_side_effect")
            return self._outcome(
                op,
                "not_applied",
                "The change was not applied. Would you like me to try again?",
            )
        return self._outcome(op, "unverified", "I could not verify whether the change went through.")
