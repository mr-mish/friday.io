"""Unified local app: one persistent agent session shared by every client.

WebSocket protocol (JSON messages):

    client -> server:
        {"type": "user", "text": "..."}                     one chat turn
        {"type": "voice", "audio": "<base64>", "format": "webm"}  local STT turn
        {"type": "interrupt"}                                barge in / cancel
        {"type": "confirm_response", "id": "...", "approved": true|false}

    server -> client:
        {"type": "text", "text": "..."}                     streamed response text
        {"type": "tool", "label": "Read(...)"}              tool activity
        {"type": "done", "cost": "$0.0123"}                 end of turn
        {"type": "confirm", "id", "tool", "detail", "reason"}   permission ask
        {"type": "sync", "messages": [...]}                   persistent history
        {"type": "audio", "pcm_b64", "sample_rate"}           local Piper audio
        {"type": "error", "message": "..."}

Confirmations fail closed: no connected client, a disconnect mid-prompt, or
a 120 s timeout all count as "declined" — same rule as the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from friday import __version__
from friday.config import FridayConfig
from friday.server.hub import AgentHub
from friday.server.voice_bridge import VoiceBridge

STATIC_DIR = Path(__file__).parent / "static"


async def _autonomy_forever(app: FastAPI, config: FridayConfig, hub: AgentHub) -> None:
    """Schedules + triggers, forever, inside the daemon (Phase 8/9)."""
    from friday.autonomy.loop import AutonomyLoop, register_maintenance
    from friday.autonomy.watcher import FileWatcher, TriggerRule

    agent = app.state.agent
    register_maintenance(agent.schedules)
    watcher = FileWatcher(
        config.granted_roots,
        config.denied_paths,
        [TriggerRule(n, t["pattern"], t["prompt"]) for n, t in config.triggers.items()],
    )

    async def notify_client(message: str) -> None:
        await hub.broadcast({"type": "notice", "message": message})

    loop = AutonomyLoop(
        config, agent.schedules, agent.inbox, watcher, agent.index, notify_client
    )
    while True:
        with contextlib.suppress(Exception):  # a bad tick must never kill the daemon
            await loop.tick()
        await asyncio.sleep(config.poll_seconds)


def create_app(
    config: FridayConfig,
    agent_factory: Callable[..., Any] | None = None,
    voice_bridge_factory: Callable[[FridayConfig], Any] | None = None,
) -> FastAPI:
    """Build the daemon app.

    `agent_factory(confirm)` must return an async-context-manager agent with
    `ask()` and a `store` attribute; tests inject fakes, production uses
    FridayAgent.
    """
    if agent_factory is None:
        from friday.agent.core import FridayAgent

        agent_factory = lambda confirm: FridayAgent(config, confirm=confirm)  # noqa: E731

    voice_bridge_factory = voice_bridge_factory or VoiceBridge
    hub = AgentHub(voice_bridge=voice_bridge_factory(config))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.agent = agent_factory(hub.confirm)
        hub.set_agent(app.state.agent)
        await app.state.agent.__aenter__()
        autonomy_task = None
        if config.autonomy_enabled:
            autonomy_task = asyncio.create_task(_autonomy_forever(app, config, hub))
        try:
            yield
        finally:
            if autonomy_task is not None:
                autonomy_task.cancel()
            await hub.close()
            await app.state.agent.__aexit__(None, None, None)

    app = FastAPI(title="FRIDAY", version=__version__, lifespan=lifespan)
    app.state.hub = hub
    app.state.agent = None

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(
            {
                "version": __version__,
                "granted_roots": [str(r) for r in config.granted_roots],
                "model": config.model,
            }
        )

    @app.get("/api/memories")
    async def memories() -> JSONResponse:
        store = getattr(app.state.agent, "store", None)
        items = store.recent() if store else []
        return JSONResponse(
            [{"id": m.id, "fact": m.fact, "created": m.created} for m in items]
        )

    @app.get("/api/inbox")
    async def inbox_endpoint() -> JSONResponse:
        inbox = getattr(app.state.agent, "inbox", None)
        items = inbox.unread() if inbox else []
        return JSONResponse(
            [{"id": n.id, "ts": n.ts, "source": n.source, "message": n.message} for n in items]
        )

    @app.get("/api/history")
    async def history_endpoint() -> JSONResponse:
        history = getattr(app.state.agent, "history", None)
        items = history.recent(limit=100) if history else []
        return JSONResponse(
            [
                {
                    "id": m.id,
                    "role": m.role,
                    "text": m.content,
                    "modality": m.modality,
                    "created": m.created,
                }
                for m in items
            ]
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        client = await hub.register(ws)
        try:
            while True:
                message = await ws.receive_json()
                message_type = message.get("type")
                if message_type == "hello":
                    client.kind = str(message.get("client", "web"))
                elif message_type == "user":
                    await hub.submit_text(client.id, str(message.get("text", "")))
                elif message_type == "voice":
                    await hub.submit_audio(
                        client.id,
                        str(message.get("audio", "")),
                        str(message.get("format", "webm")),
                    )
                elif message_type == "interrupt":
                    await hub.interrupt()
                elif message_type == "confirm_response":
                    hub.resolve(str(message.get("id")), bool(message.get("approved")))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unregister(client.id)

    return app
