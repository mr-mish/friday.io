"""File-change triggers: "when something changes in X, do Y".

Config:

    [triggers.inbox_sort]
    pattern = "~/Documents/Inbox/*"
    prompt = "New files arrived in my inbox folder: {files}. File them."

Polling (mtime-based) rather than OS file events: it needs no extra
dependency, survives editors that replace files, and a few seconds of
latency is fine for this use case. The deny list is honored — changes in
denied paths never fire a trigger.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from friday.fs.permissions import is_under


@dataclass
class TriggerRule:
    name: str
    pattern: str  # glob matched against absolute paths
    prompt: str  # may contain {files}


@dataclass
class Firing:
    rule: TriggerRule
    paths: list[str]

    @property
    def prompt(self) -> str:
        return self.rule.prompt.replace("{files}", ", ".join(self.paths))


class FileWatcher:
    def __init__(self, roots: list[Path], denied: list[Path], rules: list[TriggerRule]):
        self.roots = roots
        self.denied = denied
        self.rules = rules
        self._seen: dict[str, float] = {}
        self._primed = False

    def _scan(self) -> dict[str, float]:
        state: dict[str, float] = {}
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    if name.startswith("."):
                        continue
                    path = Path(dirpath) / name
                    if is_under(path, self.denied):
                        continue
                    try:
                        state[str(path)] = path.stat().st_mtime
                    except OSError:
                        continue
        return state

    def poll(self) -> list[Firing]:
        """Return trigger firings for files new/changed since the last poll.

        The first poll only primes the baseline — a daemon restart must not
        re-fire triggers for every existing file.
        """
        state = self._scan()
        if not self._primed:
            self._seen = state
            self._primed = True
            return []
        changed = [
            path
            for path, mtime in state.items()
            if self._seen.get(path) != mtime
        ]
        self._seen = state
        if not changed:
            return []
        firings = []
        for rule in self.rules:
            expanded = os.path.expanduser(rule.pattern)
            matches = [p for p in changed if fnmatch.fnmatch(p, expanded)]
            if matches:
                firings.append(Firing(rule, sorted(matches)))
        return firings
