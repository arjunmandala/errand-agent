"""Deterministic memory and conversation seeds for the Bengaluru errand agent."""

from datetime import datetime, timedelta

_MEMORY_TABLE = [
    ("preferred_area", "HSR Layout", "explicit", "global", "seed", None),
    ("earliest_time", "10:00", "explicit", "global", "seed", None),
    ("language", "Kannada and English", "explicit", "global", "seed", None),
    ("pets", "one indoor cat named Miso", "explicit", "global", "seed", None),
    ("parking", "basement slot B2-14, visitor bay near Gate 2", "explicit", "global", "seed", None),
    ("building", "Maple Residency, 27th Main, HSR Layout", "explicit", "global", "seed", None),
    ("floor", "4", "explicit", "global", "seed", None),
    ("landmark", "near HSR BDA Complex", "explicit", "global", "seed", None),
    ("preferred_days", "Mon,Wed,Fri", "explicit", "global", "seed", None),
    ("avoid_providers", "PRV-09, PRV-22", "explicit", "global", "seed", None),
    ("payment_preference", "UPI to the vendor app, no cash", "explicit", "global", "seed", None),
    ("contact_window", "10:00-13:00", "explicit", "global", "seed", None),
    ("lift_available", "yes, service lift until 21:00", "explicit", "global", "seed", None),
    ("old_address", "Koramangala 4th Block, previous rental", "explicit", "global", "seed", None),
    ("gym_timing", "06:30 society gym slot", "explicit", "global", "seed", None),
    ("temp_note", "scratch note: confirm gate pass later", "explicit", "global", "seed", None),
    ("society_gate", "Gate 2, intercom labelled Maple", "explicit", "global", "seed", None),
    ("quiet_hours", "14:00-16:00 no drilling", "explicit", "global", "seed", None),
    ("timezone", "Asia/Kolkata", "explicit", "global", "seed", None),
    ("society_rules", "no power tools after 18:00", "explicit", "global", "seed", None),
    ("entry_notes", "security desk needs visitor name only", "explicit", "global", "seed", None),
    ("water_hardness", "high, scale on kitchen taps", "inferred", "global", "model", True),
    ("peak_traffic", "18:00-20:00 on 27th Main", "inferred", "global", "model", False),
    ("prefers_chat", "short chat updates, not long calls", "inferred", "global", "model", True),
    ("festival_buffer", "avoid visits on local festival mornings", "inferred", "global", "model", True),
    ("dust_sensitivity", "mild dust sensitivity in bedrooms", "inferred", "global", "model", False),
    ("budget_max", "1500", "explicit", "plumber", "seed", None),
    ("preferred_plumber", "PLB-07 (Suresh N)", "explicit", "plumber", "seed", None),
    ("issue_history", "kitchen sink leak reported in October", "explicit", "plumber", "seed", None),
    ("parts_preference", "ISI marked fittings only", "explicit", "plumber", "seed", None),
    ("access_notes", "helper can open the door 10:00-12:00", "explicit", "plumber", "seed", None),
    ("visit_duration", "about 90 minutes", "explicit", "plumber", "seed", None),
    ("shutoff_location", "valve under the kitchen sink", "explicit", "plumber", "seed", None),
    ("leak_recurrence", "slow drip returns about monthly", "inferred", "plumber", "model", True),
    ("pipe_material", "likely CPVC in the kitchen run", "inferred", "plumber", "model", False),
    ("tap_brand", "looks like a Jaquar mixer", "inferred", "plumber", "model", False),
    ("preferred_electrician", "ELE-03 (Ravi Kumar)", "explicit", "electrician", "seed", None),
    ("budget_max", "1200", "explicit", "electrician", "seed", None),
    ("last_job", "living-room switchboard, October visit", "explicit", "electrician", "seed", None),
    ("spare_mcb", "one spare 16A MCB in the utility closet", "explicit", "electrician", "seed", None),
    ("panel_location", "utility closet beside the kitchen", "explicit", "electrician", "seed", None),
    ("voltage_note", "supply usually steady near 230V", "inferred", "electrician", "model", True),
    ("avoid_evening_work", "evening slots get noisy for neighbours", "inferred", "electrician", "model", False),
    ("wiring_age", "roughly eight years since last rewire", "inferred", "electrician", "model", True),
    ("budget_max", "2000", "explicit", "ac_service", "seed", None),
    ("preferred_ac_tech", "AC-11 (Meera)", "explicit", "ac_service", "seed", None),
    ("ac_brand", "Voltas 1.5 ton, two indoor units", "explicit", "ac_service", "seed", None),
    ("last_service", "filter wash done; gas top-up not needed", "explicit", "ac_service", "seed", None),
    ("indoor_units", "2", "explicit", "ac_service", "seed", None),
    ("filter_state", "filters collect dust quickly in summer", "inferred", "ac_service", "model", True),
    ("outdoor_unit", "balcony right, easy ladder access", "inferred", "ac_service", "model", False),
    ("drain_risk", "possible slow drain on the bedroom unit", "inferred", "ac_service", "model", False),
    ("budget_max", "2500", "explicit", "cleaning", "seed", None),
    ("preferred_cleaner", "CLN-04 (Lakshmi)", "explicit", "cleaning", "seed", None),
    ("deep_clean_cadence", "quarterly deep clean", "explicit", "cleaning", "seed", None),
    ("chemical_preference", "mild unscented products", "explicit", "cleaning", "seed", None),
    ("pet_safe_products", "required because of the cat", "explicit", "cleaning", "seed", None),
    ("rooms", "3BHK plus two balconies", "explicit", "cleaning", "seed", None),
    ("balcony_included", "yes, both balconies", "explicit", "cleaning", "seed", None),
    ("shoe_rack_area", "skip the shoe rack unless asked", "inferred", "cleaning", "model", False),
    ("kitchen_grease", "hob area needs extra degrease", "inferred", "cleaning", "model", True),
    ("linen_pref", "do not move linen cupboard contents", "inferred", "cleaning", "model", True),
]

_FORGET_KEYS = ("old_address", "gym_timing", "temp_note")

_CORRECTIONS = (
    ("floor", "7", "global"),
    ("contact_window", "11:00-14:00", "global"),
    ("preferred_days", "Tue,Thu,Sat", "global"),
)

_USER_LINES = (
    "Please book a plumber for the kitchen sink leak we noticed in October at Maple Residency.",
    "Ravi Kumar from ELE-03 fixed the living-room switchboard; I want the same person next time.",
    "The bedroom AC needs a routine service, filters only, no gas top-up unless they show a reading.",
    "Schedule a deep clean for the 3BHK after 10:00, mild unscented products because of the cat.",
    "Parking is basement slot B2-14 and visitors should use Gate 2 near the BDA Complex.",
    "Keep the visit inside the service budget and send a short chat update when they arrive.",
    "The helper can open the door late morning; please do not plan drilling during quiet hours.",
    "Last plumber visit was for a slow drip under the sink; ask them to check the shutoff valve.",
    "Avoid providers PRV-09 and PRV-22, and prefer weekday slots that are not Monday mornings.",
    "Confirm the service lift is fine until evening and that the outdoor AC unit is on the right balcony.",
)

_ASSIST_LINES = (
    "Noted. I will hold a plumber slot after 10:00 in HSR Layout and stay within the plumbing budget.",
    "Ravi Kumar (ELE-03) is marked as the preferred electrician for the switchboard follow-up.",
    "AC service can be a filter wash on both Voltas units; I will not add a gas top-up by default.",
    "Deep cleaning is set with pet-safe mild products, both balconies included, shoe rack left alone.",
    "Entry notes saved: Gate 2, basement visitor parking, and the landmark near HSR BDA Complex.",
    "I will ask the vendor for a short arrival chat and keep payment as UPI inside the app.",
    "Quiet hours 14:00-16:00 are blocked, and power tools will not be planned after 18:00.",
    "The October leak history and the under-sink shutoff are on the plumber brief for the next visit.",
    "Those two providers stay on the avoid list, and I will look for Tue, Thu, or Sat openings.",
    "Lift access until 21:00 and the right-balcony outdoor unit are included in the AC visit notes.",
)


_DETAIL = (
    "The visit should stay on a weekday and inside the saved budget.",
    "Please keep the arrival note short and skip a phone call.",
    "Use Gate 2 and the basement visitor bay, not the main ramp.",
    "Do not plan power tools during quiet hours or after 18:00.",
    "Leave the shoe rack and the linen cupboard unless asked.",
)


def _fit(text, index):
    body = text.strip()
    if len(body) < 80:
        body = f"{body} {_DETAIL[index % len(_DETAIL)]}"
    if len(body) < 80:
        body = body + " This is a scheduling note for the same home, not a new errand."
    if len(body) > 200:
        body = body[:197].rstrip() + "..."
    if len(body) < 80:
        body = body.ljust(80, ".")
    return body


def seed_memories(memory):
    rows = []
    for key, value, kind, scope, source, confirmed in _MEMORY_TABLE:
        row = memory.remember(
            key,
            value,
            kind=kind,
            scope=scope,
            source=source,
            confirmed=confirmed,
        )
        rows.append(row)
    for key in _FORGET_KEYS:
        memory.forget(key)
    for key, value, scope in _CORRECTIONS:
        rows.append(memory.correct(key, value, scope=scope))
    return rows


def seed_conversation(store, session_id, n=220):
    base = datetime(2026, 10, 1, 9, 0)
    topics = ("plumber leak", "switchboard", "AC service", "deep clean")
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        bank = _USER_LINES if role == "user" else _ASSIST_LINES
        topic = topics[i % len(topics)]
        raw = f"{bank[i % len(bank)]} Topic: {topic}."
        content = _fit(raw, i)
        ts = (base + timedelta(minutes=i * 37)).strftime("%Y-%m-%dT%H:%M")
        store.add_message(session_id, role, content, ts=ts)
    return n
