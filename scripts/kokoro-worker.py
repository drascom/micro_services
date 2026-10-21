#!/usr/bin/env python3
"""Persistent line-delimited JSON worker for the local Kokoro ONNX model."""

import contextlib
import json
import sys

import soundfile as sf
from kokoro_onnx import Kokoro


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: kokoro-worker.py MODEL VOICES")
    with contextlib.redirect_stdout(sys.stderr):
        tts = Kokoro(sys.argv[1], sys.argv[2])
    emit({"ready": True})
    try:
        for line in sys.stdin:
            request: dict = {}
            try:
                request = json.loads(line)
                request_id = int(request["id"])
                text = request["text"]
                output = request["output"]
                voice = request["voice"]
                speed = float(request["speed"])
                if not isinstance(text, str) or not text or not isinstance(output, str):
                    raise ValueError("invalid Kokoro request")
                if not isinstance(voice, str) or not voice:
                    raise ValueError("invalid Kokoro voice")
                with contextlib.redirect_stdout(sys.stderr):
                    audio, sample_rate = tts.create(
                        text, voice=voice, speed=speed, lang="en-us"
                    )
                    sf.write(output, audio, sample_rate, subtype="PCM_16")
                emit({"id": request_id, "ok": True})
            except Exception as error:
                emit({"id": request.get("id"), "ok": False, "error": str(error)})
    finally:
        tts.voices.close()


if __name__ == "__main__":
    main()
