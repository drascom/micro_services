# Gemma 4 12B — local server via llama.cpp

**Machine:** Apple Silicon (arm64), 24 GB unified memory, Metal. **Date:** 2026-06-08.

Serves Gemma 4 12B (Q4_0 QAT) as a local **OpenAI-compatible API** at ~30 tok/s.

## Features (all enabled & tested)

| Feature | Status | Notes |
|---------|--------|-------|
| Text chat | ✅ | |
| **Images** (vision) | ✅ | send `image_url` (data URL or http) |
| **Audio** (speech) | ✅ | send `input_audio`; llama.cpp marks audio "experimental" |
| Reasoning / thinking | ✅ | thoughts in `reasoning_content`, answer in `content` |
| Tool / function calling | ✅ | standard OpenAI `tools` array |
| Video | ❌ | model has no video encoder — unsupported |

Images & audio require the `--mmproj` projector file, which both launch scripts now
load automatically. (PDF/Text-file buttons in chat apps are handled by the *app* — it
extracts the text and sends it — not by the model's encoders.)

## How to run

```bash
./run-gemma.sh
```

…or just double-click **`Serve Gemma.command`** on the Desktop.

Then:
- **Chat UI:** http://127.0.0.1:8080  (llama.cpp's built-in web UI)
- **API for other apps:** point them at the settings below.

## Settings for other apps

| Field    | Value                          |
|----------|--------------------------------|
| Base URL | `http://127.0.0.1:8080/v1`     |
| API key  | anything (not checked)         |
| Model    | `gemma-4-12b`                  |

> Gemma 4 is a reasoning model — its chain-of-thought goes to `reasoning_content`,
> the final answer to `content`. Give it 512+ max_tokens or `content` can come back
> empty. The web UI handles this automatically.

To reach it from another device on your LAN, change `--host 127.0.0.1` to
`--host 0.0.0.0` and connect to `http://<this-mac-ip>:8080/v1`.

## Using each feature via the API

**Image:**
```json
{"role":"user","content":[
  {"type":"text","text":"What's in this image?"},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,<...>"}}
]}
```

**Audio:**
```json
{"role":"user","content":[
  {"type":"text","text":"Transcribe this."},
  {"type":"input_audio","input_audio":{"data":"<base64-wav>","format":"wav"}}
]}
```

**Tools:** pass a standard OpenAI `tools` array; the model replies with
`message.tool_calls`. (Requires `--jinja`, which the scripts set.)

In the **web UI** (http://127.0.0.1:8080) the Images and Audio attach buttons are
enabled now that the projector is loaded.

## Audio / transcription — important

Gemma 4 audio is **experimental** and Gemma is a **reasoning model**, which makes it
*erratic at transcription*: across identical runs it may transcribe correctly, claim
"there's no audio file", or return a blank answer. (It *does* hear the audio — that's
just the thinking second-guessing the noisy experimental encoder.) Quality is rough —
it garbles some words and names.

**Two ways to use it anyway:**

1. **Script (best):** `./transcribe.sh /path/to/audio.wav`
   Retries a few times until the model commits to a real transcription.

2. **In a chat UI:** the model denies the file unless you set a **System Message** like:
   > Sen ses dosyalarını duyabilen bir transkripsiyon asistanısın. Asla "dosya yok"
   > deme. Cevabın sadece duyduğun konuşmanın birebir metni olsun.

   Then attach the audio. (Most chat apps, including the one in your screenshot, have a
   System Message field.)

**For accurate transcription, use Whisper instead** — it's a dedicated speech-to-text
model, far more reliable and accurate for Turkish than Gemma's experimental audio.
Gemma's audio is best for casual "what's said here" rather than precise transcripts.

## What's in this folder

- `run-gemma.sh` — launch script (points at the GGUF already in your LM Studio folder;
  nothing was moved or copied).
- `llama.cpp-mainline/` — mainline llama.cpp, built with Metal.
  Binaries: `llama.cpp-mainline/build/bin/{llama-server,llama-cli}`.

## Model file (left in place)

`~/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/gemma-4-12b-it-qat-q4_0.gguf`

## Rebuilding llama.cpp (if ever needed)

```bash
cd llama.cpp-mainline
git pull
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_SERVER=ON
cmake --build build -j --target llama-server llama-cli
```

## Menu-bar app (macOS)

A small native menu-bar app to start/stop the server with one click — lives in `app/`.

```bash
cd app
./build.sh          # produces ../GemmaServer.app (menu-bar only, no Dock icon)
open ../GemmaServer.app
```

The menu-bar icon is grey when stopped, orange while starting, green when running.
Menu items: **Start/Stop Server**, **Open Chat UI**, **Copy LAN URL**, **Quit**.
`render-icon.swift` generates the app icon (turquoise background, coral lightning, white "G").

## Note: llama.cpp is not vendored here

The `llama.cpp-mainline/` build is **not** committed (it's large and third-party).
Clone & build it separately, then point the scripts at the `llama-server` binary:

```bash
git clone https://github.com/ggml-org/llama.cpp llama.cpp-mainline
cd llama.cpp-mainline
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_SERVER=ON
cmake --build build -j --target llama-server llama-cli
```
