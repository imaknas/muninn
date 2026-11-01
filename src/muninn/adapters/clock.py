from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    def __init__(self, tz: str):
        self._tz = ZoneInfo(tz)

    def now(self) -> datetime:
        return datetime.now(self._tz)
