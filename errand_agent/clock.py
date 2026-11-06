"""Wall clock and a deterministic stand-in for tests."""

import time
from datetime import datetime, timedelta


class Clock:
    def now(self) -> datetime:
        return datetime.now()

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
