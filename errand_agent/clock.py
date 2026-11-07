"""Wall clock and a deterministic stand-in for tests."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Clock:
    def now(self) -> datetime:
        # Naive Asia/Kolkata wall time: slots, holds and approvals are all naive IST strings.
        return datetime.now(IST).replace(tzinfo=None)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)
