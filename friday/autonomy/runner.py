"""Run one prompt unattended, safely.

Every autonomous run gets a fresh, isolated agent session with:
- a confirm policy that AUTO-DENIES anything the gate would ask about,
  recording what was declined (unattended runs never self-approve);
- a hard budget cap per run;
- its outcome written to the inbox.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from friday.autonomy.notify import Inbox
from friday.config import FridayConfig
from friday.fs.permissions import Decision

DEFAULT_BUDGET_USD = 1.0


class AutonomousRun:
    """Outcome of one unattended prompt."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.declined: list[str] = []
        self.ok = True
        self.error: str | None = None

    @property
    def summary(self) -> str:
        parts = ["\n".join(self.text).strip() or "(no output)"]
        if self.declined:
            actions = "; ".join(self.declined)
            parts.append(f"Declined without you (needs your approval): {actions}")
        if self.error:
            parts.append(f"Error: {self.error}")
        return "\n".join(parts)


async def run_autonomously(
    config: FridayConfig,
    prompt: str,
    source: str,
    inbox: Inbox,
    agent_factory: Callable[..., Any] | None = None,
    budget_usd: float = DEFAULT_BUDGET_USD,
) -> AutonomousRun:
    run = AutonomousRun()

    async def auto_deny(tool_name: str, tool_input: dict, decision: Decision) -> bool:
        detail = str(tool_input.get("command") or tool_input.get("file_path") or "")
        run.declined.append(f"{tool_name} {detail}".strip())
        return False

    if agent_factory is None:
        from friday.agent.core import FridayAgent

        def agent_factory(confirm, budget):
            return FridayAgent(config, confirm=confirm, budget_usd=budget)

    try:
        async with agent_factory(auto_deny, budget_usd) as agent:
            async for kind, payload in agent.ask(prompt):
                if kind == "text":
                    run.text.append(payload)
    except Exception as exc:
        run.ok = False
        run.error = str(exc)

    inbox.add(source, run.summary)
    return run
