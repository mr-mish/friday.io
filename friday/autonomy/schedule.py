"""Schedules: prompts that fire on their own clock.

Spec grammar (kept deliberately tiny — the agent writes these from natural
language via the schedule_task tool):

    every:30m       every N seconds/minutes/hours (s/m/h)
    daily:17:30     every day at HH:MM (local time)
    weekly:fri:17:00    every week on that day at HH:MM

Schedules whose runs fail ``MAX_FAILURES`` times in a row disable themselves
and leave an inbox notification — a stuck task must never retry forever in
silence (Phase 9 watchdog).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MAX_FAILURES = 3

_EVERY = re.compile(r"^every:(\d+)([smh])$")
_DAILY = re.compile(r"^daily:(\d{1,2}):(\d{2})$")
_WEEKLY = re.compile(r"^weekly:(" + "|".join(WEEKDAYS) + r"):(\d{1,2}):(\d{2})$")


def validate_spec(spec: str) -> None:
    if not (_EVERY.match(spec) or _DAILY.match(spec) or _WEEKLY.match(spec)):
        raise ValueError(
            f"bad schedule spec {spec!r} — use every:30m, daily:HH:MM, or weekly:day:HH:MM"
        )


def next_run(spec: str, now: datetime) -> datetime:
    """The first firing time strictly after `now`."""
    if m := _EVERY.match(spec):
        amount, unit = int(m.group(1)), m.group(2)
        seconds = amount * {"s": 1, "m": 60, "h": 3600}[unit]
        return now + timedelta(seconds=seconds)
    if m := _DAILY.match(spec):
        at = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)
    if m := _WEEKLY.match(spec):
        day = WEEKDAYS.index(m.group(1))
        at = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        at += timedelta(days=(day - at.weekday()) % 7)
        return at if at > now else at + timedelta(days=7)
    raise ValueError(f"bad schedule spec {spec!r}")


@dataclass
class Schedule:
    id: int
    name: str
    spec: str
    prompt: str
    enabled: bool
    next_run: str  # ISO local time
    last_run: str | None
    failures: int


class ScheduleStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS schedules ("
            "id INTEGER PRIMARY KEY, name TEXT UNIQUE, spec TEXT, prompt TEXT, "
            "enabled INTEGER DEFAULT 1, next_run TEXT, last_run TEXT, "
            "failures INTEGER DEFAULT 0)"
        )
        self._db.commit()

    def add(self, name: str, spec: str, prompt: str, now: datetime | None = None) -> Schedule:
        validate_spec(spec)
        now = now or datetime.now()
        fire_at = next_run(spec, now).isoformat()
        self._db.execute(
            "INSERT INTO schedules (name, spec, prompt, next_run) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET spec=excluded.spec, prompt=excluded.prompt, "
            "next_run=excluded.next_run, enabled=1, failures=0",
            (name, spec, prompt, fire_at),
        )
        self._db.commit()
        return self.get(name)

    def get(self, name: str) -> Schedule | None:
        row = self._db.execute(
            "SELECT id, name, spec, prompt, enabled, next_run, last_run, failures "
            "FROM schedules WHERE name = ?",
            (name,),
        ).fetchone()
        return _to_schedule(row) if row else None

    def cancel(self, name: str) -> bool:
        cursor = self._db.execute("DELETE FROM schedules WHERE name = ?", (name,))
        self._db.commit()
        return cursor.rowcount > 0

    def all(self) -> list[Schedule]:
        rows = self._db.execute(
            "SELECT id, name, spec, prompt, enabled, next_run, last_run, failures "
            "FROM schedules ORDER BY name"
        ).fetchall()
        return [_to_schedule(r) for r in rows]

    def due(self, now: datetime | None = None) -> list[Schedule]:
        now = now or datetime.now()
        return [
            s
            for s in self.all()
            if s.enabled and datetime.fromisoformat(s.next_run) <= now
        ]

    def mark_run(self, name: str, ok: bool, now: datetime | None = None) -> Schedule:
        """Record a run; reschedule. Returns the updated schedule (possibly
        auto-disabled by the failure watchdog)."""
        now = now or datetime.now()
        schedule = self.get(name)
        failures = 0 if ok else schedule.failures + 1
        enabled = schedule.enabled and failures < MAX_FAILURES
        self._db.execute(
            "UPDATE schedules SET last_run=?, next_run=?, failures=?, enabled=? WHERE name=?",
            (now.isoformat(), next_run(schedule.spec, now).isoformat(), failures,
             int(enabled), name),
        )
        self._db.commit()
        return self.get(name)


def _to_schedule(row: tuple) -> Schedule:
    return Schedule(
        id=row[0], name=row[1], spec=row[2], prompt=row[3],
        enabled=bool(row[4]), next_run=row[5], last_run=row[6], failures=row[7],
    )  # fmt: skip
