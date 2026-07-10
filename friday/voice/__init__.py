"""Voice pipeline: speech-to-text, text-to-speech, and the spoken session loop.

Voice dependencies are optional (`uv sync --extra voice`). Everything in this
package imports its heavy dependencies lazily so that text-mode FRIDAY and the
test suite never need them.
"""

VOICE_INSTALL_HINT = (
    "Voice mode needs the optional voice dependencies.\n"
    "Install them with:  uv sync --extra voice"
)


def voice_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        import numpy  # noqa: F401
        import piper  # noqa: F401
    except ImportError:
        return False
    return True
