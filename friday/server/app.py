"""FastAPI app: one agent session, one chat client, everything over localhost.

WebSocket protocol (JSON messages):

    client -> server:
        {"type": "user", "text": "..."}                     one chat turn
        {"type": "confirm_response", "id": "...", "approved": true|false}

    server -> client:
        {"type": "text", "text": "..."}                     streamed response text
        {"type": "tool", "label": "Read(...)"}              tool activity
        {"type": "done", "cost": "$0.0123"}                 end of turn
        {"type": "confirm", "id", "tool", "detail", "reason"}   permission ask
        {"type": "error", "message": "..."}

Confirmations fail closed: no connected client, a disconnect mid-prompt, or
a 120 s timeout all count as "declined" — same rule as the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from friday import __version__
from friday.config import FridayConfig
from friday.fs.permissions import Decision

CONFIRM_TIMEOUT_S = 120
STATIC_DIR = Path(__file__).parent / "static"


async def _autonomy_forever(app: FastAPI, config: FridayConfig, session: ChatSession) -> None:
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
        await session.send({"type": "notice", "message": message})

    loop = AutonomyLoop(
        config, agent.schedules, agent.inbox, watcher, agent.index, notify_client
    )
    while True:
        with contextlib.suppress(Exception):  # a bad tick must never kill the daemon
            await loop.tick()
        await asyncio.sleep(config.poll_seconds)


class ChatSession:
    """Bridges one WebSocket client to the agent, including confirmations."""

    def __init__(self) -> None:
        self.ws: WebSocket | None = None
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def send(self, payload: dict[str, Any]) -> None:
        if self.ws is None:
            return
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:  # client vanished mid-send; the turn keeps running
            self.drop_client()

    async def confirm(self, tool_name: str, tool_input: dict, decision: Decision) -> bool:
        """The agent's ConfirmFn: ask the connected client, fail closed."""
        if self.ws is None:
            return False
        confirm_id = uuid.uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[confirm_id] = future
        detail = str(tool_input.get("command") or tool_input.get("file_path") or "")
        await self.send(
            {
                "type": "confirm",
                "id": confirm_id,
                "tool": tool_name,
                "detail": detail,
                "reason": decision.reason,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=CONFIRM_TIMEOUT_S)
        except (TimeoutError, asyncio.CancelledError):
            return False
        finally:
            self._pending.pop(confirm_id, None)

    def resolve(self, confirm_id: str, approved: bool) -> None:
        future = self._pending.get(confirm_id)
        if future is not None and not future.done():
            future.set_result(bool(approved))

    def drop_client(self) -> None:
        self.ws = None
        for future in self._pending.values():
            if not future.done():
                future.set_result(False)  # disconnect = declined
        self._pending.clear()


def create_app(
    config: FridayConfig,
    agent_factory: Callable[..., Any] | None = None,
) -> FastAPI:
    """Build the daemon app.

    `agent_factory(confirm)` must return an async-context-manager agent with
    `ask()` and a `store` attribute; tests inject fakes, production uses
    FridayAgent.
    """
    if agent_factory is None:
        from friday.agent.core import FridayAgent

        agent_factory = lambda confirm: FridayAgent(config, confirm=confirm)  # noqa: E731

    session = ChatSession()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.agent = agent_factory(session.confirm)
        await app.state.agent.__aenter__()
        autonomy_task = None
        if config.autonomy_enabled:
            autonomy_task = asyncio.create_task(_autonomy_forever(app, config, session))
        try:
            yield
        finally:
            if autonomy_task is not None:
                autonomy_task.cancel()
            await app.state.agent.__aexit__(None, None, None)

    app = FastAPI(title="FRIDAY", version=__version__, lifespan=lifespan)
    app.state.session = session
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

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        if session.ws is not None:
            await ws.send_text(json.dumps({"type": "error", "message": "already connected"}))
            await ws.close()
            return
        session.ws = ws
        prompts: asyncio.Queue[str | None] = asyncio.Queue()

        async def receiver() -> None:
            try:
                while True:
                    message = json.loads(await ws.receive_text())
                    if message.get("type") == "user":
                        await prompts.put(str(message.get("text", "")))
                    elif message.get("type") == "confirm_response":
                        session.resolve(str(message.get("id")), bool(message.get("approved")))
            except (WebSocketDisconnect, RuntimeError):
                await prompts.put(None)  # unblock the main loop

        receive_task = asyncio.create_task(receiver())
        try:
            while True:
                prompt = await prompts.get()
                if prompt is None:
                    break
                if not prompt.strip():
                    continue
                try:
                    async for kind, payload in app.state.agent.ask(prompt):
                        key = {"text": "text", "tool": "label", "done": "cost"}.get(kind)
                        if key:
                            await session.send({"type": kind, key: payload})
                except Exception as exc:  # surface agent errors to the UI
                    await session.send({"type": "error", "message": str(exc)})
        finally:
            receive_task.cancel()
            session.drop_client()

    return app
