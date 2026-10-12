# Local files (EmbeddingGemma 2)

Gemma 4 stays the brain; EmbeddingGemma 2 (Google, Apache 2.0, 740M: text 270M + vision + audio encoders) gives it
retrieval over the user's own files. Text, images and audio share one vector space, so a text query finds a
matching document, photo, recording or video.

## Runtime
- `runtime/llama-embed/llama-server`: llama.cpp b11570 (needs ≥ PR #30054, `gemma-embedding2`). Separate from the
  Gemma 4 `runtime/llama` build.
- `models/embeddinggemma-2/embeddinggemma-2-Q8_0.gguf` (310 MB) + `mmproj-embeddinggemma-2-Q8_0.gguf` (555 MB,
  image/audio) from `ggml-org/embeddinggemma-2-GGUF`. Empty `embedding_mmproj` = text only.
- Started on demand on `embedding_url` (default `127.0.0.1:8093`): `--embeddings --pooling mean -c/-b/-ub 4096`,
  nice 10, stopped after `embedding_idle_ms`. About 1.1-1.4 GB RSS with the projector.
- Prompts from the model card: query `task: search result | query: …`, documents `title: … | text: …`, media without
  prefix. Vectors are cut to `embedding_dims` (256, MRL) and re-normalized.

## Index (`src/files-index.ts`, `data/files-index.sqlite`)
- Roots: `file_roots` (Admin → Ayarlar → Dosyalar, one path per line; default `~/Documents`). Skipped: hidden
  entries, `node_modules`, `.git`, packages/bundles (`.app`, `.photoslibrary`, …), symlinks.
- Per file: a card (name, path, type, date) plus content chunks: text/Markdown/code/CSV/JSON/HTML directly, PDF via
  `pdftotext` (optional), docx/xlsx/pptx/odt by unzipping their XML; images (downscaled via ffmpeg), audio
  (≤ 4 × 30 s, 16 kHz mono), video (4 frames + 30 s of its audio track). Files over `file_max_mb` and other types:
  card only. Without ffmpeg: JPEG/PNG/GIF/BMP and WAV/MP3 only.
- Incremental (mtime + size), removed files dropped; first pass 60 s after start, then every
  `file_index_interval_ms`; waits while a user request or task runs. Admin → Durum: counts, last run, Reindex.
- Activity log: counts and durations only (`file_index_finished`); no paths or contents.

## Tools (`.pi/extensions/files`, skill `file-assistant`)
- `file_search {query, limit?}`: top files (path, name, date, ≤ 200-char snippet). Read-only, offline.
- `file_read {path, chunk?}`: ≤ 4000 characters of text, inside the roots only (real path, no `..`, symlink or hidden).
- `file_organize`: `propose {folder?, query?}` sorts up to 50 loose files of a folder (default: first root) into the
  nearest existing subfolder (content centroid ≥ `file_organize_threshold`), clusters the rest; Gemma only names new
  folders. `apply {confirm_token}` only in the user's next turn within 2 minutes; moves only (no overwrite: ` (2)`
  suffix; no delete, no rename), skips files changed since propose, logs from → to in `data/file-actions.jsonl`.
  `undo` reverses the last apply. The full plan is shown in Admin → Durum → Son dosya planı.
- Off (`embedding_enabled: false`): no tools, no skill, no server, no indexing. `.pi/permissions.json`
  `"files": "deny"` also blocks the tools. Automated tasks get `file_search`/`file_read` only.
