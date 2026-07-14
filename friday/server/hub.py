"""One serialized agent session shared by text, web, and voice clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from friday.fs.permissions import Decision

CONFIRM_TIMEOUT_S = 120


@dataclass
class ClientConnection:
    id: str
    ws: WebSocket
    kind: str = "web"


class AgentHub:
    """Route every interactive modality through one long-lived agent.

    The Claude SDK client is a single conversational stream, so turns are
    serialized. All connected clients observe the same stream and any client
    may answer a permission prompt; confirmations still fail closed.
    """

    def __init__(self, voice_bridge: Any | None = None):
        self.agent: Any | None = None
        self.voice_bridge = voice_bridge
        self._clients: dict[str, ClientConnection] = {}
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._turn_task: asyncio.Task[None] | None = None

    def set_agent(self, agent: Any) -> None:
        self.agent = agent

    async def register(self, ws: WebSocket, kind: str = "web") -> ClientConnection:
        client = ClientConnection(uuid.uuid4().hex, ws, kind)
        self._clients[client.id] = client
        history = getattr(self.agent, "history", None)
        messages = history.recent(limit=100) if history else []
        await self.send_to(
            client.id,
            {
                "type": "sync",
                "client_id": client.id,
                "messages": [
                    {
                        "role": m.role,
                        "text": m.content,
                        "modality": m.modality,
                        "created": m.created,
                    }
                    for m in messages
                ],
            },
        )
        return client

    def unregister(self, client_id: str) -> None:
        self._clients.pop(client_id, None)
        if not self._clients:
            self._decline_pending()

    async def send_to(self, client_id: str, payload: dict[str, Any]) -> None:
        client = self._clients.get(client_id)
        if client is None:
            return
        try:
            await client.ws.send_text(json.dumps(payload))
        except Exception:
            self.unregister(client_id)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        await asyncio.gather(
            *(self.send_to(client_id, payload) for client_id in list(self._clients)),
            return_exceptions=True,
        )

    async def confirm(self, tool_name: str, tool_input: dict, decision: Decision) -> bool:
        if not self._clients:
            return False
        confirm_id = uuid.uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[confirm_id] = future
        detail = str(
            tool_input.get("command")
            or tool_input.get("file_path")
            or tool_input.get("notebook_path")
            or ""
        )
        await self.broadcast(
            {
                "type": "confirm",
                "id": confirm_id,
                "tool": tool_name,
                "detail": detail,
                "reason": decision.reason,
            }
        )
        if not self._clients:
            self.resolve(confirm_id, False)
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

    def _decline_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result(False)

    async def submit_text(self, client_id: str, prompt: str) -> bool:
        if not prompt.strip():
            return False
        if self.busy:
            await self.send_to(client_id, {"type": "busy"})
            return False
        self._turn_task = asyncio.create_task(
            self._run_turn(client_id, prompt, voice=False, announce_user=True)
        )
        return True

    async def submit_audio(
        self, client_id: str, audio_b64: str, audio_format: str = "webm"
    ) -> bool:
        if self.busy:
            await self.send_to(client_id, {"type": "busy"})
            return False
        if self.voice_bridge is None:
            await self.send_to(
                client_id,
                {
                    "type": "error",
                    "message": "Local voice is unavailable. Install with: uv sync --extra voice",
                },
            )
            return False
        self._turn_task = asyncio.create_task(
            self._run_audio_turn(client_id, audio_b64, audio_format)
        )
        return True

    @property
    def busy(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    async def _run_audio_turn(
        self, client_id: str, audio_b64: str, audio_format: str
    ) -> None:
        try:
            await self.send_to(client_id, {"type": "voice_state", "state": "transcribing"})
            transcript = await self.voice_bridge.transcribe(audio_b64, audio_format)
            if not transcript.strip():
                await self.send_to(
                    client_id, {"type": "error", "message": "I couldn't hear any speech."}
                )
                return
            await self.broadcast({"type": "transcript", "text": transcript})
            await self._run_turn(client_id, transcript, voice=True, announce_user=False)
        except Exception as exc:
            await self.send_to(client_id, {"type": "error", "message": str(exc)})
        finally:
            await self.send_to(client_id, {"type": "voice_state", "state": "idle"})

    async def _run_turn(
        self, client_id: str, prompt: str, voice: bool, announce_user: bool
    ) -> None:
        if self.agent is None:
            await self.send_to(client_id, {"type": "error", "message": "Agent is not ready."})
            return
        if announce_user:
            await self.broadcast({"type": "user", "text": prompt, "source": client_id})
        await self.broadcast({"type": "turn_start", "source": client_id, "voice": voice})
        sentence_stream = None
        if voice and self.voice_bridge is not None:
            sentence_stream = self.voice_bridge.sentence_stream()
            await self.send_to(client_id, {"type": "voice_state", "state": "thinking"})
        done_payload = ""
        try:
            async for kind, payload in self.agent.ask(prompt):
                if kind == "done":
                    done_payload = payload
                    continue
                key = {"text": "text", "tool": "label", "done": "cost"}.get(kind)
                if key:
                    await self.broadcast({"type": kind, key: payload})
                if kind == "text" and sentence_stream is not None:
                    for sentence in sentence_stream.feed(payload):
                        await self._send_audio(client_id, sentence)
            if sentence_stream is not None:
                for sentence in sentence_stream.flush():
                    await self._send_audio(client_id, sentence)
            await self.broadcast({"type": "done", "cost": done_payload})
        except asyncio.CancelledError:
            await self.broadcast({"type": "interrupted"})
            raise
        except Exception as exc:
            await self.broadcast({"type": "error", "message": str(exc)})

    async def _send_audio(self, client_id: str, sentence: str) -> None:
        await self.send_to(client_id, {"type": "voice_state", "state": "speaking"})
        for message in await self.voice_bridge.synthesize(sentence):
            await self.send_to(client_id, message)

    async def interrupt(self) -> None:
        if self.agent is not None and hasattr(self.agent, "interrupt"):
            with contextlib.suppress(Exception):
                await self.agent.interrupt()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()

    async def close(self) -> None:
        self._decline_pending()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
