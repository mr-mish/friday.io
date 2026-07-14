"""Thin WebSocket client used by terminal interfaces to join the daemon."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

type ConfirmCallback = Callable[[dict], Awaitable[bool]]


class DaemonClient:
    def __init__(self, url: str, confirm_callback: ConfirmCallback | None = None):
        self.url = url
        self.ws = None
        self.client_id: str | None = None
        self.confirm_callback = confirm_callback

    async def connect(self) -> None:
        from websockets.asyncio.client import connect

        self.ws = await connect(self.url, open_timeout=0.5)
        first = json.loads(await self.ws.recv())
        if first.get("type") == "sync":
            self.client_id = first.get("client_id")
        await self.ws.send(json.dumps({"type": "hello", "client": "cli"}))

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def send_user(self, text: str) -> None:
        await self.ws.send(json.dumps({"type": "user", "text": text}))

    async def confirm(self, confirm_id: str, approved: bool) -> None:
        await self.ws.send(
            json.dumps(
                {"type": "confirm_response", "id": confirm_id, "approved": approved}
            )
        )

    async def interrupt(self) -> None:
        await self.ws.send(json.dumps({"type": "interrupt"}))

    async def events(self) -> AsyncIterator[dict]:
        async for raw in self.ws:
            yield json.loads(raw)

    async def ask(self, prompt: str) -> AsyncIterator[tuple[str, str]]:
        """Agent-compatible turn API for the text and local voice CLIs."""
        await self.send_user(prompt)
        active = False
        async for message in self.events():
            kind = message.get("type")
            if kind == "confirm":
                approved = (
                    await self.confirm_callback(message) if self.confirm_callback else False
                )
                await self.confirm(str(message.get("id")), approved)
            elif kind == "turn_start":
                active = message.get("source") == self.client_id
            elif kind == "text" and active:
                yield ("text", str(message.get("text", "")))
            elif kind == "tool" and active:
                yield ("tool", str(message.get("label", "")))
            elif kind == "done" and active:
                yield ("done", str(message.get("cost", "")))
                return
            elif kind == "busy":
                raise RuntimeError("FRIDAY is already handling another turn")
            elif kind == "error":
                raise RuntimeError(str(message.get("message", "daemon error")))
