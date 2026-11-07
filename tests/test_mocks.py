"""Contract tests for the mock tool environment (PDF section 2)."""
from collections import Counter
from datetime import datetime, timedelta

import pytest

from errand_agent.clock import FakeClock
from errand_agent.mocks.env import DEFAULT_USER_ID, MockToolEnv, ScenarioConfig, ToolTimeout, ServiceUnavailable

START = datetime(2026, 11, 10, 8, 0)
WEEK = ["2026-11-10", "2026-11-14"]


def make_env(**cfg):
    return MockToolEnv(seed=7, clock=FakeClock(START), config=ScenarioConfig(**cfg))


def first_slot(env, service="plumber"):
    p = env.search_providers(service, "HSR Layout", WEEK)["providers"][0]
    return p, p["slots"][0]


def test_search_returns_3_to_8_providers_with_required_fields():
    env = make_env()
    for service in ["plumber", "electrician", "ac_service"]:
        res = env.search_providers(service, "HSR Layout", WEEK)
        assert 3 <= len(res["providers"]) <= 8
        for p in res["providers"]:
            assert {"id", "name", "price", "rating", "slots", "attributes", "service", "area"} <= set(p)
            assert p["service"] == service
            for s in p["slots"]:
                assert WEEK[0] <= s[:10] <= WEEK[1]


def test_get_provider_has_reviews_and_at_least_one_injection():
    env = make_env()
    ids = [p["id"] for p in env.search_providers("plumber", "HSR Layout", WEEK)["providers"]]
    reviews = [r["text"] for pid in ids for r in env.get_provider(pid)["reviews"]]
    assert any("ignore" in r.lower() and "instruction" in r.lower() for r in reviews)


def test_hold_slot_returns_hold_with_10_minute_expiry_and_quoted_price():
    env = make_env()
    p, slot = first_slot(env)
    h = env.hold_slot(p["id"], slot)
    assert h["hold_id"]
    assert datetime.fromisoformat(h["expires_at"]) == START + timedelta(minutes=10)
    assert h["quoted_price"] == p["price"]


def test_hold_slot_price_drift_is_configurable():
    env = make_env(price_drift={"*": 200})
    p, slot = first_slot(env)
    assert env.hold_slot(p["id"], slot)["quoted_price"] == p["price"] + 200


def test_hold_unknown_slot_is_error():
    env = make_env()
    p, _ = first_slot(env)
    assert env.hold_slot(p["id"], "2030-01-01T09:00")["error"] == "slot_unavailable"


def test_book_outcome_distribution_70_15_15_half_of_timeouts_booked():
    env = make_env()
    c = Counter()
    for i in range(4000):
        c[env._roll_book_outcome()] += 1
    n = sum(c.values())
    assert abs(c["success"] / n - 0.70) < 0.03
    assert abs(c["slot_taken"] / n - 0.15) < 0.03
    timeouts = c["timeout_booked"] + c["timeout_not_booked"]
    assert abs(timeouts / n - 0.15) < 0.03
    assert abs(c["timeout_booked"] / timeouts - 0.5) < 0.1


def test_book_success_and_idempotent_on_same_key():
    env = make_env(book_outcomes=["success"])
    p, slot = first_slot(env)
    h = env.hold_slot(p["id"], slot)
    r1 = env.book(h["hold_id"], "k1")
    r2 = env.book(h["hold_id"], "k1")
    assert r1["status"] == "confirmed" and r1 == r2
    assert len([b for b in env.list_bookings(DEFAULT_USER_ID)["bookings"] if b["status"] == "confirmed" and b["slot"] == slot]) == 1


def test_book_slot_taken():
    env = make_env(book_outcomes=["slot_taken"])
    p, slot = first_slot(env)
    h = env.hold_slot(p["id"], slot)
    assert env.book(h["hold_id"], "k1")["error"] == "slot_taken"


def test_book_timeout_after_success_is_visible_in_status():
    env = make_env(book_outcomes=["timeout_booked"])
    p, slot = first_slot(env)
    h = env.hold_slot(p["id"], slot)
    with pytest.raises(ToolTimeout):
        env.book(h["hold_id"], "k1")
    assert env.get_booking_status(idempotency_key="k1")["status"] == "confirmed"
    assert env.get_booking_status(hold_id=h["hold_id"])["status"] == "confirmed"


def test_book_timeout_not_booked_status_not_found():
    env = make_env(book_outcomes=["timeout_not_booked"])
    p, slot = first_slot(env)
    h = env.hold_slot(p["id"], slot)
    with pytest.raises(ToolTimeout):
        env.book(h["hold_id"], "k1")
    assert env.get_booking_status(idempotency_key="k1")["status"] == "not_found"


def test_book_hold_expired_after_10_minutes():
    env = make_env(book_outcomes=["success"])
    p, slot = first_slot(env)
    h = env.hold_slot(p["id"], slot)
    env.clock.advance(601)
    assert env.book(h["hold_id"], "k1")["error"] == "hold_expired"


def test_cancel_first_call_503_then_success_idempotent():
    env = make_env(book_outcomes=["success"])
    p, slot = first_slot(env)
    b = env.book(env.hold_slot(p["id"], slot)["hold_id"], "k1")
    with pytest.raises(ServiceUnavailable):
        env.cancel(b["booking_id"], "c1")
    r = env.cancel(b["booking_id"], "c1")
    assert r["status"] == "cancelled"
    assert env.cancel(b["booking_id"], "c1")["status"] == "cancelled"


def test_reschedule_conflict_on_configured_slot_and_success_otherwise():
    env = make_env(book_outcomes=["success"], reschedule_conflict_slots={"2026-11-14T09:00"})
    p, slot = first_slot(env)
    b = env.book(env.hold_slot(p["id"], slot)["hold_id"], "k1")
    assert env.reschedule(b["booking_id"], "2026-11-14T09:00", "r1")["error"] == "slot_conflict"
    ok = env.reschedule(b["booking_id"], "2026-11-14T11:00", "r2")
    assert ok["status"] == "confirmed" and ok["slot"] == "2026-11-14T11:00"


def test_list_bookings_has_seeded_history_with_required_fields():
    env = make_env()
    bs = env.list_bookings(DEFAULT_USER_ID)["bookings"]
    assert bs
    for b in bs:
        assert {"id", "provider_id", "provider_name", "service", "slot", "status"} <= set(b)
    assert env.list_bookings("someone_else")["bookings"] == []


def test_provider_cancellation_event():
    env = make_env(book_outcomes=["success"])
    p, slot = first_slot(env)
    b = env.book(env.hold_slot(p["id"], slot)["hold_id"], "k1")
    env.provider_cancels(b["booking_id"])
    st = [x for x in env.list_bookings(DEFAULT_USER_ID)["bookings"] if x["id"] == b["booking_id"]][0]
    assert st["status"] == "cancelled_by_provider"


def test_seeded_runs_are_reproducible():
    a, b = make_env(), make_env()
    assert [a._roll_book_outcome() for _ in range(50)] == [b._roll_book_outcome() for _ in range(50)]
