"""Single source of trusted time.

All GMP timestamps are generated server-side in UTC (never taken from the
client) so records are contemporaneous and cannot be back-dated by users.
Tests monkeypatch ``now`` to simulate the passage of time.
"""

from datetime import date, datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def today() -> date:
    return now().date()
