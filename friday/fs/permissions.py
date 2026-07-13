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
    # TodoWrite only manages the agent's in-memory task list — it touches no
    # files, so treat it as a READ-tier no-op instead of confirming every call.
    "TodoWrite": (Tier.READ, []),
    "Bash": (Tier.DANGEROUS, []),
    "WebFetch": (Tier.DANGEROUS, []),
    "WebSearch": (Tier.DANGEROUS, []),
    # FRIDAY's own memory tools (friday/memory/tools.py): in-process, no
    # filesystem paths — the file index already enforces the deny list.
    "mcp__memory__remember": (Tier.WRITE, []),
    "mcp__memory__recall": (Tier.READ, []),
    "mcp__memory__forget": (Tier.WRITE, []),
    "mcp__memory__search_files": (Tier.READ, []),
    # Built-in autonomy tools: schedules always execute under the
    # auto-deny-confirms policy, so creating them is bounded-risk.
    "mcp__autonomy__schedule_task": (Tier.WRITE, []),
    "mcp__autonomy__list_schedules": (Tier.READ, []),
    "mcp__autonomy__cancel_schedule": (Tier.WRITE, []),
    "mcp__autonomy__check_inbox": (Tier.READ, []),
}

# Patterns in Bash commands that are flat-out denied rather than confirmable.
# Interactive confirmation cannot make these safe enough for v0.
_BASH_HARD_DENY = re.compile(
    r"""
      rm\s+-[a-z]*r[a-z]*f                              # rm -rf and friends
    | mkfs                                              # format a filesystem
    | dd\s+if=                                          # raw disk copy
    | :\(\)\s*\{                                        # classic fork bomb
    | chmod\s+-R\s+777                                  # world-writable, recursive
    | \bshutdown\b | \breboot\b                         # power state
    | \bsudo\b                                          # privilege escalation
    | (?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|python[0-9.]*)\b  # pipe to shell
    | >\s*/dev/(?:sd|nvme|disk|hd|vd)                   # write to a block device
    | git\s+push\b[^\n]*(?:--force\b|--force-with-lease\b|\s-f\b)  # force push
    """,
    re.VERBOSE,
)


def is_under(path: Path, ancestors: list[Path]) -> bool:
    return any(path == a or a in path.parents for a in ancestors)


class PermissionGate:
    def __init__(self, config: FridayConfig):
        self.config = config

    def evaluate(self, tool_name: str, tool_input: dict) -> Decision:
        if tool_name in ("WebSearch", "WebFetch") and self.config.allow_web:
            return Decision(Verdict.ALLOW, Tier.READ, "web access enabled in config")
        if tool_name not in TOOL_RULES and (skill := self._evaluate_skill_tool(tool_name)):
            return skill
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

    def _evaluate_skill_tool(self, tool_name: str) -> Decision | None:
        """Policy for tools from user-configured MCP skill servers.

        Trusted skills ("allow") run without prompting; everything else —
        including tools from servers we don't recognize — falls through to
        the default confirm-everything rule.
        """
        if not tool_name.startswith("mcp__"):
            return None
        parts = tool_name.split("__", 2)
        server = parts[1] if len(parts) > 1 else ""
        skill = self.config.skills.get(server)
        if skill and skill.trust == "allow":
            return Decision(Verdict.ALLOW, Tier.READ, f"trusted skill '{server}'")
        return None

    def _evaluate_bash(self, command: str) -> Decision:
        if _BASH_HARD_DENY.search(command):
            return Decision(Verdict.DENY, Tier.DANGEROUS, "destructive shell pattern")
        # Deny-list paths must not appear anywhere in a shell command — including
        # via a relative path that resolves into a denied location.
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for token in tokens:
            for candidate in self._path_candidates(token):
                if is_under(candidate, self.config.denied_paths):
                    return Decision(
                        Verdict.DENY, Tier.DANGEROUS, f"{candidate} is on the deny list"
                    )
        return Decision(Verdict.CONFIRM, Tier.DANGEROUS, "shell command requires confirmation")

    def _path_candidates(self, token: str) -> list[Path]:
        """Filesystem paths a shell token might refer to, for deny-list scanning.

        Absolute and ``~`` tokens resolve directly. A relative path-like token
        (contains ``/`` or starts with ``.``) is resolved against each granted
        root and the home dir, so ``../.ssh/id_rsa`` or ``secrets/key.pem``
        can't sneak a denied path past the scan. Bare words (flags, subcommands)
        are ignored.
        """
        if not token or token.startswith("-"):
            return []
        raw: list[Path] = []
        if token.startswith(("/", "~")):
            raw.append(Path(token).expanduser())
        elif "/" in token or token.startswith("."):
            raw = [base / token for base in (*self.config.granted_roots, Path.home())]
        resolved: list[Path] = []
        for candidate in raw:
            try:
                resolved.append(candidate.resolve())
            except OSError:
                continue
        return resolved
