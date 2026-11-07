"""In-process mock of the errand tool environment (seeded, scenario-driven)."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from errand_agent.clock import Clock
from errand_agent.mocks.data import PROVIDERS, providers_for


DEFAULT_USER_ID = "demo-user"


class ToolTimeout(Exception):
    """Tool call timed out. The side effect may or may not have landed."""


class ServiceUnavailable(Exception):
    """Transient upstream failure."""

    status = 503

    def __init__(self, message: str = "service unavailable"):
        super().__init__(message)
        self.status = 503


@dataclass
class ScenarioConfig:
    price_drift: dict = field(default_factory=dict)
    book_outcomes: list = field(default_factory=list)
    reschedule_conflict_slots: set = field(default_factory=set)
    cancel_fail_first: bool = True
    status_unavailable: bool = False
    user_id: str = DEFAULT_USER_ID


class MockToolEnv:
    def __init__(self, seed: int = 0, clock=None, config: ScenarioConfig | None = None):
        self.rng = random.Random(seed)
        self.clock = clock if clock is not None else Clock()
        self.config = config if config is not None else ScenarioConfig()
        self.calls: list[tuple[str, dict]] = []
        self.side_effects = 0
        self._holds: dict[str, dict] = {}
        self._bookings: dict[str, dict] = {}
        self._booking_by_hold: dict[str, str] = {}
        self._booking_by_key: dict[str, str] = {}
        self._book_confirmations: dict[str, dict] = {}
        self._unavailable_slots: set[tuple[str, str]] = set()
        self._cancel_failed: set[str] = set()
        self._cancel_results: dict[str, dict] = {}
        self._reschedule_results: dict[str, dict] = {}
        self._seed_history()

    def _record(self, name: str, args: dict) -> None:
        self.calls.append((name, args))

    def _hex_id(self, prefix: str) -> str:
        return prefix + f"{self.rng.randrange(16**6):06x}"

    def _seed_history(self) -> None:
        seeds = [
            {
                "id": "BK-SEED-EL",
                "provider_id": "ELE-03",
                "slot": "2026-10-20T10:00",
                "status": "completed",
                "price": PROVIDERS["ELE-03"]["price"],
                "user_id": DEFAULT_USER_ID,
            },
            {
                "id": "BK-SEED-PL",
                "provider_id": "PLB-01",
                "slot": "2026-10-18T08:00",
                "status": "completed",
                "price": PROVIDERS["PLB-01"]["price"],
                "user_id": DEFAULT_USER_ID,
            },
            {
                "id": "BK-SEED-AC",
                "provider_id": "ACS-01",
                "slot": "2026-11-12T10:00",
                "status": "confirmed",
                "price": PROVIDERS["ACS-01"]["price"],
                "user_id": DEFAULT_USER_ID,
            },
        ]
        for raw in seeds:
            provider = PROVIDERS[raw["provider_id"]]
            booking = {
                "id": raw["id"],
                "provider_id": raw["provider_id"],
                "provider_name": provider["name"],
                "service": provider["service"],
                "slot": raw["slot"],
                "status": raw["status"],
                "price": raw["price"],
                "user_id": raw["user_id"],
                "hold_id": None,
                "idempotency_key": None,
            }
            self._bookings[booking["id"]] = booking
            self._unavailable_slots.add((booking["provider_id"], booking["slot"]))

    def _dates(self, date_range: list[str]) -> list[str]:
        start = datetime.strptime(date_range[0], "%Y-%m-%d").date()
        end = datetime.strptime(date_range[1], "%Y-%m-%d").date()
        dates: list[str] = []
        day = start
        while day <= end and len(dates) < 7:
            dates.append(day.isoformat())
            day += timedelta(days=1)
        return dates

    def _offered_slot(self, provider_id: str, slot: str) -> bool:
        provider = PROVIDERS.get(provider_id)
        if provider is None:
            return False
        try:
            when = datetime.fromisoformat(slot)
        except ValueError:
            return False
        if when.strftime("%Y-%m-%dT%H:%M") != slot:
            return False
        if when.hour not in provider["hours"] or when.minute != 0:
            return False
        if (provider_id, slot) in self._unavailable_slots:
            return False
        return True

    def _quoted_price(self, provider_id: str) -> int:
        base = PROVIDERS[provider_id]["price"]
        drift = self.config.price_drift
        delta = drift.get(provider_id, drift.get("*", 0))
        return base + delta

    def _next_outcome(self) -> str:
        if self.config.book_outcomes:
            return self.config.book_outcomes.pop(0)
        return self._roll_book_outcome()

    def _roll_book_outcome(self) -> str:
        roll = self.rng.random()
        if roll < 0.70:
            return "success"
        if roll < 0.85:
            return "slot_taken"
        if roll < 0.925:
            return "timeout_booked"
        return "timeout_not_booked"

    def _create_booking(self, hold: dict, idempotency_key: str) -> dict:
        provider = PROVIDERS[hold["provider_id"]]
        # From the idempotency key, not the seeded RNG, so a restarted mock with the same seed cannot reuse an ID.
        booking_id = "BK-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:10]
        booking = {
            "id": booking_id,
            "provider_id": hold["provider_id"],
            "provider_name": provider["name"],
            "service": provider["service"],
            "slot": hold["slot"],
            "status": "confirmed",
            "price": hold["quoted_price"],
            "user_id": self.config.user_id,
            "hold_id": hold["hold_id"],
            "idempotency_key": idempotency_key,
        }
        self._bookings[booking_id] = booking
        self._booking_by_hold[hold["hold_id"]] = booking_id
        self._booking_by_key[idempotency_key] = booking_id
        self._unavailable_slots.add((hold["provider_id"], hold["slot"]))
        self.side_effects += 1
        confirmation = {
            "status": "confirmed",
            "booking_id": booking_id,
            "provider_id": hold["provider_id"],
            "slot": hold["slot"],
            "price": hold["quoted_price"],
            "hold_id": hold["hold_id"],
        }
        self._book_confirmations[idempotency_key] = confirmation
        return confirmation

    def _booking_view(self, booking: dict) -> dict:
        return {
            "id": booking["id"],
            "provider_id": booking["provider_id"],
            "provider_name": booking["provider_name"],
            "service": booking["service"],
            "slot": booking["slot"],
            "status": booking["status"],
            "price": booking["price"],
        }

    def search_providers(self, service: str, area: str, date_range: list[str]) -> dict:
        self._record(
            "search_providers",
            {"service": service, "area": area, "date_range": list(date_range)},
        )
        if service not in {p["service"] for p in PROVIDERS.values()}:
            return {"providers": [], "error": "unknown_service"}
        dates = self._dates(date_range)
        providers = []
        for provider in providers_for(service, area)[:8]:
            slots = []
            for day in dates:
                for hour in sorted(provider["hours"]):
                    slot = f"{day}T{hour:02d}:00"
                    if (provider["id"], slot) in self._unavailable_slots:
                        continue
                    slots.append(slot)
            providers.append(
                {
                    "id": provider["id"],
                    "name": provider["name"],
                    "service": provider["service"],
                    "area": area,
                    "price": provider["price"],
                    "rating": provider["rating"],
                    "slots": slots,
                    "attributes": {
                        "verified": provider["attributes"]["verified"],
                        "years_experience": provider["attributes"]["years_experience"],
                        "technician": provider["attributes"]["technician"],
                        "languages": list(provider["attributes"]["languages"]),
                    },
                }
            )
        return {"providers": providers}

    def get_provider(self, provider_id: str) -> dict:
        self._record("get_provider", {"provider_id": provider_id})
        provider = PROVIDERS.get(provider_id)
        if provider is None:
            return {"error": "not_found"}
        return {
            "id": provider["id"],
            "name": provider["name"],
            "service": provider["service"],
            "areas": list(provider["areas"]),
            "price": provider["price"],
            "rating": provider["rating"],
            "hours": list(provider["hours"]),
            "attributes": {
                "verified": provider["attributes"]["verified"],
                "years_experience": provider["attributes"]["years_experience"],
                "technician": provider["attributes"]["technician"],
                "languages": list(provider["attributes"]["languages"]),
            },
            "reviews": [
                {"author": r["author"], "rating": r["rating"], "text": r["text"]}
                for r in provider["reviews"]
            ],
        }

    def hold_slot(self, provider_id: str, slot: str) -> dict:
        self._record("hold_slot", {"provider_id": provider_id, "slot": slot})
        if provider_id not in PROVIDERS:
            return {"error": "not_found"}
        if not self._offered_slot(provider_id, slot):
            return {"error": "slot_unavailable"}
        hold_id = self._hex_id("H-")
        expires = self.clock.now() + timedelta(minutes=10)
        hold = {
            "hold_id": hold_id,
            "provider_id": provider_id,
            "slot": slot,
            "quoted_price": self._quoted_price(provider_id),
            "expires_at": expires,
        }
        self._holds[hold_id] = hold
        return {
            "hold_id": hold_id,
            "provider_id": provider_id,
            "slot": slot,
            "quoted_price": hold["quoted_price"],
            "expires_at": expires.isoformat(),
        }

    def book(self, hold_id: str, idempotency_key: str) -> dict:
        self._record("book", {"hold_id": hold_id, "idempotency_key": idempotency_key})
        if idempotency_key in self._book_confirmations:
            return dict(self._book_confirmations[idempotency_key])
        hold = self._holds.get(hold_id)
        if hold is None:
            return {"error": "unknown_hold"}
        if self.clock.now() > hold["expires_at"]:
            return {"error": "hold_expired"}
        outcome = self._next_outcome()
        if outcome == "success":
            return dict(self._create_booking(hold, idempotency_key))
        if outcome == "slot_taken":
            self._unavailable_slots.add((hold["provider_id"], hold["slot"]))
            return {"error": "slot_taken"}
        if outcome == "timeout_booked":
            self._create_booking(hold, idempotency_key)
            raise ToolTimeout("book timed out after success")
        if outcome == "timeout_not_booked":
            raise ToolTimeout("book timed out")
        return {"error": outcome}

    def get_booking_status(self, hold_id: str | None = None, idempotency_key: str | None = None) -> dict:
        self._record(
            "get_booking_status",
            {"hold_id": hold_id, "idempotency_key": idempotency_key},
        )
        if self.config.status_unavailable:
            raise ServiceUnavailable("booking status unavailable")
        booking_id = None
        if idempotency_key is not None:
            booking_id = self._booking_by_key.get(idempotency_key)
        if booking_id is None and hold_id is not None:
            booking_id = self._booking_by_hold.get(hold_id)
        booking = self._bookings.get(booking_id) if booking_id else None
        if booking is None:
            return {"status": "not_found"}
        return {
            "status": booking["status"],
            "booking_id": booking["id"],
            "provider_id": booking["provider_id"],
            "slot": booking["slot"],
            "price": booking["price"],
            "hold_id": booking.get("hold_id"),
        }

    def cancel(self, booking_id: str, idempotency_key: str) -> dict:
        self._record(
            "cancel",
            {"booking_id": booking_id, "idempotency_key": idempotency_key},
        )
        if idempotency_key in self._cancel_results:
            return dict(self._cancel_results[idempotency_key])
        booking = self._bookings.get(booking_id)
        if booking is None:
            return {"error": "not_found"}
        if self.config.cancel_fail_first and booking_id not in self._cancel_failed:
            self._cancel_failed.add(booking_id)
            raise ServiceUnavailable("cancel failed")
        booking["status"] = "cancelled"
        result = {"status": "cancelled", "booking_id": booking_id}
        self._cancel_results[idempotency_key] = result
        return dict(result)

    def reschedule(self, booking_id: str, slot: str, idempotency_key: str) -> dict:
        self._record(
            "reschedule",
            {"booking_id": booking_id, "slot": slot, "idempotency_key": idempotency_key},
        )
        if idempotency_key in self._reschedule_results:
            return dict(self._reschedule_results[idempotency_key])
        booking = self._bookings.get(booking_id)
        if booking is None:
            return {"error": "not_found"}
        if slot in self.config.reschedule_conflict_slots:
            return {"error": "slot_conflict"}
        booking["slot"] = slot
        self._unavailable_slots.add((booking["provider_id"], slot))
        result = {"status": "confirmed", "booking_id": booking_id, "slot": slot}
        self._reschedule_results[idempotency_key] = result
        return dict(result)

    def list_bookings(self, user_id: str) -> dict:
        self._record("list_bookings", {"user_id": user_id})
        if user_id != self.config.user_id:
            return {"bookings": []}
        bookings = [
            self._booking_view(b)
            for b in self._bookings.values()
            if b["user_id"] == user_id
        ]
        return {"bookings": bookings}

    def provider_cancels(self, booking_id: str) -> None:
        self._record("provider_cancels", {"booking_id": booking_id})
        booking = self._bookings.get(booking_id)
        if booking is not None:
            booking["status"] = "cancelled_by_provider"
