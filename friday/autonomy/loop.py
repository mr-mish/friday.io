"""The autonomy loop the daemon runs: schedules, triggers, maintenance.

`tick()` is a single synchronous-logic pass (easy to test); the daemon calls
it every `poll_seconds`. Built-in maintenance (Phase 9) registers as ordinary
schedules whose prompts start with "@" — those run in-process instead of
spawning an agent:

    @refresh_index          incremental file-index refresh
    @consolidate_memories   agent pass that prunes/merges stale memories
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from friday.autonomy.notify import Inbox
from friday.autonomy.runner import run_autonomously
from friday.autonomy.schedule import ScheduleStore
from friday.autonomy.watcher import FileWatcher
from friday.config import FridayConfig
from friday.memory.index import FileIndex

CONSOLIDATE_PROMPT = (
    "Review your stored memories with the recall tool. Merge duplicates and "
    "forget anything obsolete or superseded, using the forget and remember "
    "tools. Finish with one sentence describing what you cleaned up."
)

MAINTENANCE = {
    "maintenance:index": ("every:30m", "@refresh_index"),
    "maintenance:memories": ("weekly:sun:03:00", "@consolidate_memories"),
}


def register_maintenance(store: ScheduleStore) -> None:
    for name, (spec, prompt) in MAINTENANCE.items():
        if store.get(name) is None:
            store.add(name, spec, prompt)


class AutonomyLoop:
    def __init__(
        self,
        config: FridayConfig,
        store: ScheduleStore,
        inbox: Inbox,
        watcher: FileWatcher,
        index: FileIndex,
        notify_client: Callable[[str], Awaitable[None]] | None = None,
        run=run_autonomously,
    ):
        self.config = config
        self.store = store
        self.inbox = inbox
        self.watcher = watcher
        self.index = index
        self._notify_client = notify_client
        self._run = run  # injectable for tests

    async def _announce(self, message: str) -> None:
        if self._notify_client is not None:
            await self._notify_client(message)

    async def _execute(self, source: str, prompt: str) -> bool:
        if prompt == "@refresh_index":
            self.index.refresh()
            return True
        if prompt == "@consolidate_memories":
            prompt = CONSOLIDATE_PROMPT
        run = await self._run(
            self.config, prompt, source, self.inbox, budget_usd=self.config.budget_usd
        )
        await self._announce(f"{source}: {run.summary.splitlines()[0][:200]}")
        return run.ok

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        for schedule in self.store.due(now):
            ok = False
            try:
                ok = await self._execute(f"schedule:{schedule.name}", schedule.prompt)
            finally:
                updated = self.store.mark_run(schedule.name, ok, now)
                if not updated.enabled and schedule.enabled:
                    self.inbox.add(
                        "watchdog",
                        f"Schedule '{schedule.name}' failed {updated.failures} times "
                        "in a row and was disabled.",
                    )
        for firing in self.watcher.poll():
            await self._execute(f"trigger:{firing.rule.name}", firing.prompt)
