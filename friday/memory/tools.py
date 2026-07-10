"""FRIDAY's memory, exposed to the agent as in-process MCP tools.

These run inside the FRIDAY process (no subprocess, no network) and only
touch FRIDAY's own SQLite database plus the already-permission-checked file
index — which is why the permission gate auto-allows them.
"""

from __future__ import annotations

import time
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from friday.memory.index import FileIndex
from friday.memory.store import MemoryStore

REFRESH_INTERVAL_S = 60


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def build_memory_server(store: MemoryStore, index: FileIndex) -> McpSdkServerConfig:
    last_refresh = 0.0

    @tool(
        "remember",
        "Store a lasting fact or preference about the user or their world, phrased as a "
        "complete standalone sentence. Use when the user states something worth keeping "
        "across sessions.",
        {"fact": str},
    )
    async def remember(args: dict[str, Any]) -> dict[str, Any]:
        memory_id = store.remember(str(args["fact"]))
        return _text(f"Remembered (id {memory_id}).")

    @tool(
        "recall",
        "Search FRIDAY's long-term memories for facts relevant to a topic.",
        {"query": str},
    )
    async def recall(args: dict[str, Any]) -> dict[str, Any]:
        memories = store.search(str(args["query"]))
        if not memories:
            return _text("No matching memories.")
        return _text("\n".join(f"[{m.id}] {m.fact} ({m.created[:10]})" for m in memories))

    @tool(
        "forget",
        "Delete a stored memory by its id (from recall). Use when the user asks you to "
        "forget something or a fact is outdated.",
        {"memory_id": int},
    )
    async def forget(args: dict[str, Any]) -> dict[str, Any]:
        ok = store.forget(int(args["memory_id"]))
        return _text("Forgotten." if ok else "No memory with that id.")

    @tool(
        "search_files",
        "Search the contents of the user's granted folders by keywords. Returns file "
        "paths with matching snippets. Use when the user asks to find a file by what's "
        "in it rather than by its name.",
        {"query": str},
    )
    async def search_files(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal last_refresh
        if time.monotonic() - last_refresh > REFRESH_INTERVAL_S:
            index.refresh()
            last_refresh = time.monotonic()
        hits = index.search(str(args["query"]))
        if not hits:
            return _text("No files matched.")
        return _text("\n\n".join(f"{h.path}\n  …{h.snippet}…" for h in hits))

    return create_sdk_mcp_server(
        name="memory", version="1.0.0", tools=[remember, recall, forget, search_files]
    )
