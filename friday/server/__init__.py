"""The FRIDAY daemon: a localhost HTTP + WebSocket API around the agent.

This is the UI-agnostic backend from docs/PLAN.md §3 — the browser chat
panel talks to it today, and a native tray app (Tauri) can wrap the same
API later. Server dependencies are optional (`uv sync --extra server`).
"""

SERVER_INSTALL_HINT = (
    "The FRIDAY daemon needs the optional server dependencies.\n"
    "Install them with:  uv sync --extra server"
)


def server_available() -> bool:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return False
    return True
