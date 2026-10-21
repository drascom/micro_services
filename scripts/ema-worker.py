#!/usr/bin/env python3
"""Persistent line-delimited JSON worker for the local EMA Lightning model."""

import contextlib
import json
import sys

from ema_lightning import EMA


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main() -> None:
    device = sys.argv[1] if len(sys.argv) > 1 else "cpu"
    with contextlib.redirect_stdout(sys.stderr):
        tts = EMA(device=device)
    emit({"ready": True})
    for line in sys.stdin:
        request: dict = {}
        try:
            request = json.loads(line)
            request_id = int(request["id"])
            text = request["text"]
            output = request["output"]
            speed = float(request["speed"])
            seed = int(request["seed"])
            if not isinstance(text, str) or not text or not isinstance(output, str):
                raise ValueError("invalid EMA request")
            with contextlib.redirect_stdout(sys.stderr):
                tts.say(text, speed=speed, seed=seed, sample_rate=48000, path=output)
            emit({"id": request_id, "ok": True})
        except Exception as error:
            emit({"id": request.get("id"), "ok": False, "error": str(error)})


if __name__ == "__main__":
    main()
