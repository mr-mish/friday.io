"""Microphone capture and speaker playback via sounddevice/PortAudio.

This is the only module that touches real audio hardware, so it is kept
thin: everything above it works with numpy arrays and can be tested headless.
"""

from __future__ import annotations

import queue
import threading

from friday.voice.stt import SAMPLE_RATE


class Recorder:
    """Capture mono float32 audio at 16 kHz between start() and stop()."""

    def __init__(self):
        import sounddevice as sd

        self._sd = sd
        self._frames: list = []
        self._stream: object | None = None

    def start(self) -> None:
        self._frames = []

        def callback(indata, _frames, _time, _status):
            self._frames.append(indata.copy())

        self._stream = self._sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
        )
        self._stream.start()

    def stop(self):
        import numpy as np

        self._stream.stop()
        self._stream.close()
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames)[:, 0]


class FrameStream:
    """Continuous mic capture as fixed-size frames (for wake word + VAD)."""

    def __init__(self, frame_ms: int = 30):
        import sounddevice as sd

        self._sd = sd
        self._frames: queue.Queue = queue.Queue(maxsize=256)
        self._blocksize = SAMPLE_RATE * frame_ms // 1000
        self._stream = None

    def start(self) -> None:
        def callback(indata, _frames, _time, _status):
            import contextlib

            with contextlib.suppress(queue.Full):  # consumer stalled; drop rather than lag
                self._frames.put_nowait(indata[:, 0].copy())

        self._stream = self._sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=self._blocksize,
            callback=callback,
        )
        self._stream.start()

    def next(self):
        """Blocking read of the next frame (call via asyncio.to_thread)."""
        return self._frames.get()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()


class Player:
    """Sequential playback queue with interruption (barge-in) support."""

    def __init__(self, sample_rate: int):
        import sounddevice as sd

        self._sd = sd
        self.sample_rate = sample_rate
        self._queue: queue.Queue = queue.Queue()
        self._interrupted = threading.Event()
        self._playing = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    @property
    def active(self) -> bool:
        """True while FRIDAY is speaking (drives the echo guard)."""
        return self._playing.is_set() or not self._queue.empty()

    def play(self, samples) -> None:
        """Queue an int16 chunk for playback."""
        self._queue.put(samples)

    def interrupt(self) -> None:
        """Stop current playback and drop everything queued."""
        self._interrupted.set()
        self._sd.stop()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        self._interrupted.clear()

    def wait(self) -> None:
        """Block until everything queued so far has been played."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            samples = self._queue.get()
            try:
                if not self._interrupted.is_set():
                    self._playing.set()
                    self._sd.play(samples, samplerate=self.sample_rate, blocking=True)
            finally:
                self._playing.clear()
                self._queue.task_done()
