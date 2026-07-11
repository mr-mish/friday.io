"""Voice self-test: run `friday --doctor` on the machine that will speak.

Downloads the configured models if needed, synthesizes a phrase, transcribes
it back, and reports timings — proving the local voice stack works before you
ever open the microphone. Model downloads need normal internet access
(huggingface.co), so run this on your own machine, not in a restricted
sandbox.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from friday.config import FridayConfig

TEST_PHRASE = "Hello boss. Phase two is online, and all systems are operational."


def run(config: FridayConfig) -> int:
    from friday.voice import VOICE_INSTALL_HINT, voice_available

    if not voice_available():
        print(VOICE_INSTALL_HINT)
        return 1

    from friday.voice.stt import Transcriber
    from friday.voice.tts import Speaker, ensure_voice

    print(f"1/4  Piper voice '{config.tts_voice}' …", flush=True)
    try:
        voice_path = ensure_voice(config.tts_voice, config.voices_dir)
    except Exception:
        print(
            "\nFAIL — could not download the voice model; the underlying error is\n"
            "printed above. Common causes:\n"
            "  - CERTIFICATE_VERIFY_FAILED: your Python has no CA certificates\n"
            "    (python.org macOS builds). Reinstall voice deps to pull in\n"
            "    certifi (uv sync --extra voice), or run\n"
            '    "/Applications/Python 3.13/Install Certificates.command".\n'
            "  - No access to huggingface.co (restricted network/sandbox):\n"
            "    run this on your own machine, or pre-place the model in\n"
            f"    {config.voices_dir}"
        )
        _report_handsfree_and_autonomy(config)
        return 1
    speaker = Speaker(voice_path)

    print("2/4  synthesizing …", flush=True)
    t0 = time.perf_counter()
    wav = Path(config.data_dir) / "doctor_check.wav"
    speaker.synthesize_wav(TEST_PHRASE, wav)
    tts_s = time.perf_counter() - t0

    print(f"3/4  whisper '{config.stt_model}' transcribing it back …", flush=True)
    transcriber = Transcriber(config.stt_model, language=config.language)
    t0 = time.perf_counter()
    heard = transcriber.transcribe(wav)
    stt_s = time.perf_counter() - t0

    print("4/4  checking microphone/speakers …", flush=True)
    try:
        import sounddevice as sd

        devices = f"in={sd.default.device[0]} out={sd.default.device[1]}"
    except Exception as exc:  # no PortAudio / no devices — still report the rest
        devices = f"unavailable ({exc})"

    normalized = "".join(c for c in heard.lower() if c.isalnum() or c == " ")
    ok = "phase two is online" in normalized
    print()
    print(f"said : {TEST_PHRASE}")
    print(f"heard: {heard}")
    print(f"tts  : {tts_s:.2f}s   stt: {stt_s:.2f}s   audio devices: {devices}")
    print("PASS — voice stack is working" if ok else "FAIL — transcription did not match")
    _report_handsfree_and_autonomy(config)
    return 0 if ok else 1


def _report_handsfree_and_autonomy(config: FridayConfig) -> None:
    """Extra sections: the always-on stack (Phases 6-9)."""
    from friday.voice.vad import EnergyVAD, UtteranceCollector
    from friday.voice.verify import verifier_available
    from friday.voice.wakeword import wakeword_available

    print("\nhands-free stack:")
    # VAD self-test with synthetic audio: silence, a loud burst, silence.
    import numpy as np

    vad = EnergyVAD()
    collector = UtteranceCollector(vad.is_speech, silence_ms=90, min_speech_ms=60)
    frame = 480  # 30ms @ 16kHz
    rng = np.random.default_rng(0)
    utterance = None
    for kind in ["quiet"] * 10 + ["loud"] * 10 + ["quiet"] * 10:
        amplitude = 0.005 if kind == "quiet" else 0.5
        got = collector.feed((rng.standard_normal(frame) * amplitude).astype(np.float32))
        utterance = utterance if got is None else got
    print(f"  vad turn-taking : {'ok' if utterance is not None else 'FAILED'} (synthetic)")
    wake = "installed" if wakeword_available() else "not installed (see voice/wakeword.py hint)"
    print(f"  wake word       : {wake}")
    if verifier_available():
        from friday.voice.verify import SpeakerVerifier

        verifier = SpeakerVerifier(config.data_dir, config.verify_threshold)
        state = (
            "enrolled" if verifier.enrolled
            else "installed, NOT enrolled (friday --enroll-voice)"
        )
    else:
        state = "not installed (uv sync --extra handsfree)"
    print(f"  speaker verify  : {state}")

    from friday.autonomy.notify import Inbox
    from friday.autonomy.schedule import ScheduleStore

    schedules = ScheduleStore(config.db_path).all()
    disabled = [s for s in schedules if not s.enabled]
    unread = len(Inbox(config.db_path).unread())
    print("autonomy:")
    print(f"  schedules       : {len(schedules)} ({len(disabled)} disabled by watchdog)")
    print(f"  inbox unread    : {unread}")


if __name__ == "__main__":
    from friday.config import load_config

    sys.exit(run(load_config()))
