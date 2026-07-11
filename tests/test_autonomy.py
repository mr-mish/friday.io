from datetime import datetime, time

import pytest

from friday.autonomy.loop import MAINTENANCE, AutonomyLoop, register_maintenance
from friday.autonomy.notify import Inbox, in_quiet_hours
from friday.autonomy.runner import run_autonomously
from friday.autonomy.schedule import ScheduleStore, next_run, validate_spec
from friday.autonomy.watcher import FileWatcher, TriggerRule
from friday.config import FridayConfig

NOW = datetime(2026, 7, 10, 12, 0, 0)  # a Friday


# ------------------------------------------------------------------ schedules


def test_spec_validation():
    for good in ("every:30m", "every:10s", "daily:07:30", "weekly:fri:17:00"):
        validate_spec(good)
    for bad in ("hourly", "every:30", "daily:25:00x", "weekly:funday:17:00"):
        with pytest.raises(ValueError):
            validate_spec(bad)


def test_next_run_math():
    assert next_run("every:30m", NOW) == datetime(2026, 7, 10, 12, 30)
    assert next_run("daily:07:30", NOW) == datetime(2026, 7, 11, 7, 30)  # already past today
    assert next_run("daily:17:00", NOW) == datetime(2026, 7, 10, 17, 0)
    assert next_run("weekly:fri:17:00", NOW) == datetime(2026, 7, 10, 17, 0)
    assert next_run("weekly:fri:09:00", NOW) == datetime(2026, 7, 17, 9, 0)  # next week


def test_store_add_due_mark(tmp_path):
    store = ScheduleStore(tmp_path / "friday.db")
    store.add("weekly", "weekly:fri:17:00", "Summarize my week.", now=NOW)
    assert store.due(NOW) == []
    due = store.due(datetime(2026, 7, 10, 17, 1))
    assert [s.name for s in due] == ["weekly"]

    updated = store.mark_run("weekly", ok=True, now=datetime(2026, 7, 10, 17, 1))
    assert updated.failures == 0
    assert updated.next_run.startswith("2026-07-17T17:00")


def test_failure_watchdog_disables_after_three(tmp_path):
    store = ScheduleStore(tmp_path / "friday.db")
    store.add("flaky", "every:1m", "x", now=NOW)
    for _ in range(3):
        updated = store.mark_run("flaky", ok=False, now=NOW)
    assert updated.failures == 3
    assert updated.enabled is False


# ---------------------------------------------------------------------- inbox


def test_inbox_roundtrip(tmp_path):
    inbox = Inbox(tmp_path / "friday.db")
    inbox.add("schedule:weekly", "All done.")
    notices = inbox.unread()
    assert len(notices) == 1 and notices[0].source == "schedule:weekly"
    inbox.mark_read([notices[0].id])
    assert inbox.unread() == []


def test_quiet_hours_overnight_wrap():
    assert in_quiet_hours("22:00-08:00", time(23, 30)) is True
    assert in_quiet_hours("22:00-08:00", time(7, 59)) is True
    assert in_quiet_hours("22:00-08:00", time(12, 0)) is False
    assert in_quiet_hours("09:00-17:00", time(12, 0)) is True
    assert in_quiet_hours("", time(3, 0)) is False
    with pytest.raises(ValueError):
        in_quiet_hours("late-night", time(3, 0))


# --------------------------------------------------------------------- runner


class FakeAgent:
    def __init__(self, confirm):
        self._confirm = confirm

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def ask(self, prompt):
        from friday.fs.permissions import Decision, Tier, Verdict

        approved = await self._confirm(
            "Bash", {"command": "rm x"}, Decision(Verdict.CONFIRM, Tier.DANGEROUS, "confirm")
        )
        yield ("text", f"tried a dangerous thing: approved={approved}")
        yield ("done", "")


async def test_autonomous_runs_never_self_approve(tmp_path):
    inbox = Inbox(tmp_path / "friday.db")
    run = await run_autonomously(
        FridayConfig(),
        "clean things up",
        "schedule:test",
        inbox,
        agent_factory=lambda confirm, budget: FakeAgent(confirm),
    )
    assert "approved=False" in run.summary
    assert run.declined == ["Bash rm x"]
    notices = inbox.unread()
    assert len(notices) == 1
    assert "Declined without you" in notices[0].message


# -------------------------------------------------------------------- watcher


def test_watcher_fires_on_change_not_on_startup(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    existing = root / "old.txt"
    existing.write_text("here before the watcher")
    rule = TriggerRule("inbox", str(root / "*.txt"), "New: {files}")
    watcher = FileWatcher([root], [], [rule])

    assert watcher.poll() == []  # priming poll must not fire on existing files

    new = root / "fresh.txt"
    new.write_text("hello")
    firings = watcher.poll()
    assert len(firings) == 1
    assert firings[0].paths == [str(new)]
    assert str(new) in firings[0].prompt
    assert watcher.poll() == []  # no re-fire without a change


def test_watcher_honors_deny_list(tmp_path):
    root = tmp_path / "docs"
    secret = root / "private"
    secret.mkdir(parents=True)
    rule = TriggerRule("all", str(root / "*"), "{files}")
    watcher = FileWatcher([root], [secret], [rule])
    watcher.poll()
    (secret / "key.txt").write_text("secret")
    assert watcher.poll() == []


# ----------------------------------------------------------------------- loop


async def test_loop_runs_due_schedule_and_reschedules(tmp_path):
    config = FridayConfig(data_dir=tmp_path)
    store = ScheduleStore(config.db_path)
    inbox = Inbox(config.db_path)
    store.add("greet", "every:1m", "say hi", now=NOW)
    ran = []

    async def fake_run(config, prompt, source, inbox_, budget_usd):
        ran.append((source, prompt))
        inbox_.add(source, "did it")

        class R:
            ok = True
            summary = "did it"

        return R()

    loop = AutonomyLoop(
        config, store, inbox,
        FileWatcher([], [], []),
        index=None, notify_client=None, run=fake_run,
    )  # fmt: skip
    await loop.tick(datetime(2026, 7, 10, 12, 2))
    assert ran == [("schedule:greet", "say hi")]
    assert store.get("greet").next_run.startswith("2026-07-10T12:03")


async def test_loop_watchdog_notifies_on_disable(tmp_path):
    config = FridayConfig(data_dir=tmp_path)
    store = ScheduleStore(config.db_path)
    inbox = Inbox(config.db_path)
    store.add("broken", "every:1m", "explode", now=NOW)

    async def failing_run(config, prompt, source, inbox_, budget_usd):
        class R:
            ok = False
            summary = "boom"

        return R()

    loop = AutonomyLoop(
        config, store, inbox, FileWatcher([], [], []), index=None, run=failing_run
    )
    when = datetime(2026, 7, 10, 12, 2)
    for _ in range(3):
        await loop.tick(when)
        when = datetime.fromisoformat(store.get("broken").next_run)
    schedule = store.get("broken")
    assert schedule.enabled is False
    assert any("disabled" in n.message for n in inbox.unread())


def test_maintenance_registration(tmp_path):
    store = ScheduleStore(tmp_path / "friday.db")
    register_maintenance(store)
    names = {s.name for s in store.all()}
    assert set(MAINTENANCE) <= names
    register_maintenance(store)  # idempotent
    assert len(store.all()) == len(names)
