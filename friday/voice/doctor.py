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
            "\nFAIL — could not download the voice model. Model downloads need\n"
            "access to huggingface.co; run this on your own machine (not a\n"
            "restricted sandbox) or pre-place the model in "
            f"{config.voices_dir}"
        )
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
    return 0 if ok else 1


if __name__ == "__main__":
    from friday.config import load_config

    sys.exit(run(load_config()))
