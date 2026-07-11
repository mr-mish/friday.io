"""Scheduling exposed to the agent as in-process MCP tools.

"Remind me every Friday at five to review the week" becomes a
schedule_task call. Created schedules always run under the autonomous
policy (auto-deny on anything the gate would confirm), so letting the
agent create them is bounded-risk and auto-allowed by the gate.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from friday.autonomy.notify import Inbox
from friday.autonomy.schedule import ScheduleStore


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def build_autonomy_server(store: ScheduleStore, inbox: Inbox) -> McpSdkServerConfig:
    @tool(
        "schedule_task",
        "Create or replace a recurring autonomous task. spec is one of "
        "'every:30m' (s/m/h), 'daily:HH:MM', or 'weekly:mon:HH:MM'. The prompt runs "
        "unattended: actions needing confirmation are declined and reported to the "
        "user's inbox instead.",
        {"name": str, "spec": str, "prompt": str},
    )
    async def schedule_task(args: dict[str, Any]) -> dict[str, Any]:
        try:
            schedule = store.add(str(args["name"]), str(args["spec"]), str(args["prompt"]))
        except ValueError as exc:
            return _text(f"Invalid schedule: {exc}")
        return _text(
            f"Scheduled '{schedule.name}' ({schedule.spec}); next run {schedule.next_run}."
        )

    @tool("list_schedules", "List all recurring autonomous tasks.", {})
    async def list_schedules(_args: dict[str, Any]) -> dict[str, Any]:
        schedules = store.all()
        if not schedules:
            return _text("No schedules.")
        lines = [
            f"{s.name}: {s.spec} — next {s.next_run}"
            + ("" if s.enabled else f" (DISABLED after {s.failures} failures)")
            for s in schedules
        ]
        return _text("\n".join(lines))

    @tool("cancel_schedule", "Delete a recurring autonomous task by name.", {"name": str})
    async def cancel_schedule(args: dict[str, Any]) -> dict[str, Any]:
        ok = store.cancel(str(args["name"]))
        return _text("Cancelled." if ok else "No schedule with that name.")

    @tool(
        "check_inbox",
        "Read the user's unread notifications from autonomous runs (marks them read).",
        {},
    )
    async def check_inbox(_args: dict[str, Any]) -> dict[str, Any]:
        notices = inbox.unread()
        if not notices:
            return _text("Inbox empty.")
        inbox.mark_read([n.id for n in notices])
        return _text("\n\n".join(f"[{n.ts[:16]}] {n.source}\n{n.message}" for n in notices))

    return create_sdk_mcp_server(
        name="autonomy",
        version="1.0.0",
        tools=[schedule_task, list_schedules, cancel_schedule, check_inbox],
    )
