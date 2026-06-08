# Gemma Server (macOS menu-bar app)

A tiny macOS app that runs **Gemma 4 12B** locally and serves an **OpenAI-compatible API**.
Click the menu-bar icon to start/stop it — no terminal needed.

- Endpoint: `http://127.0.0.1:8080/v1` (also reachable on your LAN)
- Model id: `gemma-4-12b`
- API key: anything (not checked)
- Supports text, images, audio, reasoning, and tool calls.

## Install

```bash
git clone https://github.com/drascom/micro_services
cd micro_services/gemma
./install.sh
```

`install.sh` does everything for you:
- builds **llama.cpp** (or reuses an existing build) — you never touch it
- builds **GemmaServer.app**
- writes a local `config.env`

Then launch it:

```bash
open GemmaServer.app
```

To keep it around, drag **GemmaServer.app** into your **Applications** folder.

> **Model:** `install.sh` **downloads the Gemma GGUF automatically** from Hugging Face
> into `~/.cache/gemma-server/models`. That model is gated, so if the download fails the
> installer prints the license page + direct file links and asks you to accept the
> license and `huggingface-cli login`, then re-run.
> Already have the file? Run `GEMMA_MODEL=/path/to/model.gguf ./install.sh`.

## Use

A **⚡G** icon appears in the menu bar:

- **grey** = stopped · **orange** = starting · **green** = running
- **Start / Stop Server** — toggle the server
- **Open Chat UI** — opens `http://127.0.0.1:8080`
- **Copy LAN URL** — copies the address for other devices on your network
- **Quit** — stops the server and exits

### Use it from another app

| Field    | Value                      |
|----------|----------------------------|
| Base URL | `http://127.0.0.1:8080/v1` |
| Model    | `gemma-4-12b`              |
| API key  | anything                   |

## Requirements

- Apple Silicon Mac, macOS 13+
- Xcode Command Line Tools (`xcode-select --install`)
- `cmake` — `install.sh` offers to install it via Homebrew if missing

## Extras

- **CLI start** (no app): `./run-gemma.sh`
- **Audio transcription**: `./transcribe.sh /path/to/audio.wav`
  (Gemma's audio is experimental — for accurate transcripts use Whisper.)

## Configuration

`install.sh` writes `config.env` (gitignored). Override defaults by exporting before install:

```bash
GEMMA_PORT=9000 GEMMA_CTX=16384 ./install.sh
```

`GEMMA_MODEL`, `GEMMA_MMPROJ`, `GEMMA_HOST`, `GEMMA_PORT`, `GEMMA_CTX` are all supported.
