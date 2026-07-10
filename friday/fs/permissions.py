"""The permission gate: every tool call the agent makes passes through here.

Three tiers (see docs/PLAN.md §6):

- READ      — allowed automatically inside granted roots
- WRITE     — allowed automatically inside granted roots
- DANGEROUS — always requires explicit user confirmation (shell commands,
              deletions, anything outside granted roots)

The deny list always wins, at every tier — a denied path cannot even be
confirmed interactively.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from friday.config import FridayConfig


class Tier(Enum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


class Verdict(Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"  # ask the user before proceeding
    DENY = "deny"


@dataclass
class Decision:
    verdict: Verdict
    tier: Tier
    reason: str
    paths: list[Path] = field(default_factory=list)


# Agent SDK tool name -> (tier, input keys that carry filesystem paths)
TOOL_RULES: dict[str, tuple[Tier, list[str]]] = {
    "Read": (Tier.READ, ["file_path"]),
    "Glob": (Tier.READ, ["path"]),
    "Grep": (Tier.READ, ["path"]),
    "Write": (Tier.WRITE, ["file_path"]),
    "Edit": (Tier.WRITE, ["file_path"]),
    "MultiEdit": (Tier.WRITE, ["file_path"]),
    "NotebookEdit": (Tier.WRITE, ["notebook_path"]),
    "Bash": (Tier.DANGEROUS, []),
    "WebFetch": (Tier.DANGEROUS, []),
    "WebSearch": (Tier.DANGEROUS, []),
    # FRIDAY's own memory tools (friday/memory/tools.py): in-process, no
    # filesystem paths — the file index already enforces the deny list.
    "mcp__memory__remember": (Tier.WRITE, []),
    "mcp__memory__recall": (Tier.READ, []),
    "mcp__memory__forget": (Tier.WRITE, []),
    "mcp__memory__search_files": (Tier.READ, []),
}

# Substrings in Bash commands that are flat-out denied rather than confirmable.
# Interactive confirmation cannot make these safe enough for v0.
_BASH_HARD_DENY = re.compile(
    r"(rm\s+-[a-z]*r[a-z]*f|mkfs|dd\s+if=|:\(\)\s*\{|chmod\s+-R\s+777|shutdown|reboot)"
)


def is_under(path: Path, ancestors: list[Path]) -> bool:
    return any(path == a or a in path.parents for a in ancestors)


class PermissionGate:
    def __init__(self, config: FridayConfig):
        self.config = config

    def evaluate(self, tool_name: str, tool_input: dict) -> Decision:
        tier, path_keys = TOOL_RULES.get(tool_name, (Tier.DANGEROUS, []))
        paths = [
            Path(str(tool_input[k])).expanduser().resolve()
            for k in path_keys
            if tool_input.get(k)
        ]

        # The deny list wins at every tier, including over confirmation.
        for p in paths:
            if is_under(p, self.config.denied_paths):
                return Decision(Verdict.DENY, tier, f"{p} is on the deny list", paths)

        if tool_name == "Bash":
            return self._evaluate_bash(str(tool_input.get("command", "")))

        if tier is Tier.DANGEROUS:
            return Decision(Verdict.CONFIRM, tier, f"{tool_name} requires confirmation", paths)

        outside = [p for p in paths if not is_under(p, self.config.granted_roots)]
        if outside:
            return Decision(
                Verdict.CONFIRM,
                tier,
                f"{outside[0]} is outside granted roots",
                paths,
            )
        return Decision(Verdict.ALLOW, tier, f"{tier.value} within granted roots", paths)

    def _evaluate_bash(self, command: str) -> Decision:
        if _BASH_HARD_DENY.search(command):
            return Decision(Verdict.DENY, Tier.DANGEROUS, "destructive shell pattern")
        # Deny-list paths must not appear anywhere in a shell command.
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for token in tokens:
            if token.startswith(("/", "~")):
                p = Path(token).expanduser()
                try:
                    p = p.resolve()
                except OSError:
                    continue
                if is_under(p, self.config.denied_paths):
                    return Decision(
                        Verdict.DENY, Tier.DANGEROUS, f"{p} is on the deny list"
                    )
        return Decision(Verdict.CONFIRM, Tier.DANGEROUS, "shell command requires confirmation")
