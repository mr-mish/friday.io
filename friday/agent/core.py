"""Agent core: a Claude Agent SDK loop wired to FRIDAY's permission gate.

The SDK provides the agentic loop and the file tools (Read, Glob, Grep,
Write, Edit, Bash). FRIDAY contributes policy: every tool call is routed
through ``PermissionGate`` via the SDK's ``can_use_tool`` callback, journaled
to the audit log, and — when the gate says CONFIRM — held until the user
approves it through the ``confirm`` callback supplied by the interface (CLI
today, voice later).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from friday.config import FridayConfig
from friday.fs.audit import AuditLog
from friday.fs.permissions import Decision, PermissionGate, Verdict
from friday.fs.undo import UndoJournal
from friday.memory.index import FileIndex
from friday.memory.store import MemoryStore
from friday.memory.tools import build_memory_server

# What the user hears/reads is produced from these event tuples so that the
# CLI and the voice pipeline can render the same stream differently.
type AgentEvent = tuple[str, str]  # (kind, payload) — kind: "text" | "tool" | "done"

type ConfirmFn = Callable[[str, dict, Decision], Awaitable[bool]]

FRIDAY_TOOLS = [
    "Read", "Glob", "Grep", "Write", "Edit", "Bash", "TodoWrite", "WebSearch", "WebFetch",
]  # fmt: skip

BASE_SYSTEM_PROMPT = """\
You are FRIDAY, a personal assistant running on the user's own computer.
You speak concisely and warmly, like a highly competent chief of staff.
Your responses may be read aloud by a text-to-speech engine, so prefer
flowing prose over markdown tables, headers, or code blocks unless the
user is clearly working with code.

You have tools to search, read, and edit the user's files. Filesystem
access is governed by a permission system outside your control: reads and
writes inside the folders the user has granted are automatic, everything
else pauses and asks them. Never try to work around a denied action —
explain what was denied and let the user decide.

You may only act on the user's files within these granted folders:
{roots}

You have long-term memory. When the user states a lasting preference or fact
("my accountant is Dana", "always export invoices as PDF"), store it with the
remember tool without being asked. Use recall to look things up, forget when
the user retracts something, and search_files to find files by their content.
{memories}"""


def _skill_servers(config: FridayConfig) -> dict:
    """User-configured MCP skills ([skills.NAME] in friday.toml) -> SDK config."""
    servers: dict = {}
    for name, skill in config.skills.items():
        if name == "memory":
            continue  # reserved for FRIDAY's built-in memory server
        if skill.url:
            servers[name] = {"type": "http", "url": skill.url}
        elif skill.command:
            servers[name] = {
                "type": "stdio",
                "command": skill.command,
                "args": skill.args,
                "env": skill.env,
            }
    return servers


def _system_prompt(config: FridayConfig, store: MemoryStore) -> str:
    roots = "\n".join(f"- {r}" for r in config.granted_roots) or "- (none granted yet)"
    memories = store.recent(limit=30)
    memory_block = ""
    if memories:
        lines = "\n".join(f"- [{m.id}] {m.fact}" for m in memories)
        memory_block = f"\nWhat you currently remember:\n{lines}\n"
    prompt = BASE_SYSTEM_PROMPT.format(roots=roots, memories=memory_block)
    if config.system_prompt_extra:
        prompt += "\n" + config.system_prompt_extra
    return prompt


class FridayAgent:
    """One conversational session. Create, then ``async with`` it."""

    def __init__(self, config: FridayConfig, confirm: ConfirmFn):
        self.config = config
        self.gate = PermissionGate(config)
        self.audit = AuditLog(config.audit_log_path)
        self.store = MemoryStore(config.db_path)
        self.index = FileIndex(config.db_path, config.granted_roots, config.denied_paths)
        self.undo = UndoJournal(config.data_dir)
        self._confirm = confirm
        cwd = config.granted_roots[0] if config.granted_roots else Path.home()
        # `tools` sets what exists; `allowed_tools` would AUTO-APPROVE calls
        # before can_use_tool runs, silently bypassing the permission gate.
        self._client = ClaudeSDKClient(
            ClaudeAgentOptions(
                system_prompt=_system_prompt(config, self.store),
                tools=FRIDAY_TOOLS,
                mcp_servers={
                    "memory": build_memory_server(self.store, self.index),
                    **_skill_servers(config),
                },
                model=config.model,
                cwd=str(cwd),
                can_use_tool=self._can_use_tool,
                # Policy is enforced in the PreToolUse hook, not can_use_tool:
                # the CLI auto-approves some calls (reads in cwd, "safe" shell
                # commands) without ever consulting can_use_tool, but hooks see
                # every call. The hook maps the gate's verdict to allow/deny,
                # or "ask" — which routes to can_use_tool for the user prompt.
                hooks={"PreToolUse": [HookMatcher(hooks=[self._policy_hook])]},
                setting_sources=[],  # never inherit the host's Claude Code settings
            )
        )

    async def __aenter__(self) -> FridayAgent:
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.disconnect()

    async def _policy_hook(
        self, hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        tool_name = str(hook_input.get("tool_name", ""))
        tool_input = hook_input.get("tool_input") or {}
        decision = self.gate.evaluate(tool_name, tool_input)
        self.audit.record(
            "tool_call",
            tool=tool_name,
            input=tool_input,
            verdict=decision.verdict.value,
            tier=decision.tier.value,
            reason=decision.reason,
        )
        if decision.verdict is Verdict.ALLOW:
            self._snapshot_for_undo(tool_name, tool_input)
        permission = {
            Verdict.ALLOW: "allow",
            Verdict.CONFIRM: "ask",  # routes to _can_use_tool, which prompts
            Verdict.DENY: "deny",
        }[decision.verdict]
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": permission,
                "permissionDecisionReason": decision.reason,
            }
        }

    async def _can_use_tool(
        self, tool_name: str, tool_input: dict, _context: ToolPermissionContext
    ) -> PermissionResult:
        decision = self.gate.evaluate(tool_name, tool_input)
        if decision.verdict is Verdict.DENY:
            return PermissionResultDeny(message=f"Denied by policy: {decision.reason}")
        if decision.verdict is Verdict.CONFIRM:
            approved = await self._confirm(tool_name, tool_input, decision)
            self.audit.record(
                "confirmation", tool=tool_name, approved=approved, reason=decision.reason
            )
            if not approved:
                return PermissionResultDeny(message="The user declined this action.")
        self._snapshot_for_undo(tool_name, tool_input)
        return PermissionResultAllow()

    def _snapshot_for_undo(self, tool_name: str, tool_input: dict) -> None:
        """Journal the pre-write state so `friday --undo` can revert it."""
        if tool_name in ("Write", "Edit") and tool_input.get("file_path"):
            self.undo.record_change(Path(str(tool_input["file_path"])))

    async def ask(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """Send one user turn; yield events as the response streams in."""
        await self._client.query(prompt)
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield ("text", block.text)
                    elif isinstance(block, ToolUseBlock):
                        yield ("tool", _describe_tool(block))
            elif isinstance(message, ResultMessage):
                cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else ""
                yield ("done", cost)

    async def interrupt(self) -> None:
        await self._client.interrupt()


def _describe_tool(block: ToolUseBlock) -> str:
    arg = (
        block.input.get("file_path")
        or block.input.get("path")
        or block.input.get("pattern")
        or block.input.get("command")
        or block.input.get("query")
        or block.input.get("fact")
        or ""
    )
    arg = str(arg)
    if len(arg) > 80:
        arg = arg[:77] + "..."
    return f"{block.name}({arg})"
