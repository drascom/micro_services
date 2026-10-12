# Local files (EmbeddingGemma 2)

Gemma 4 stays the brain; EmbeddingGemma 2 (Google, Apache 2.0, 740M: text 270M + vision + audio encoders) gives it
retrieval over the user's own files. Text, images and audio share one vector space, so a text query finds a
matching document, photo, recording or video.

## Runtime
- `runtime/llama/llama-server`: the same llama.cpp build as Gemma 4 (pinned in `scripts/install-llama.sh`, needs
  ≥ PR #30054, `gemma-embedding2`).
- `models/embeddinggemma-2/embeddinggemma-2-Q8_0.gguf` (310 MB) + `mmproj-embeddinggemma-2-Q8_0.gguf` (555 MB,
  image/audio) from `ggml-org/embeddinggemma-2-GGUF`. Empty `embedding_mmproj` = text only.
- Started on demand on `embedding_url` (default `127.0.0.1:8093`): `--embeddings --pooling mean -c/-b/-ub 4096`,
  nice 10, stopped after `embedding_idle_ms`. About 1.1-1.4 GB RSS with the projector.
- Prompts from the model card: query `task: search result | query: …`, documents `title: … | text: …`, media without
  prefix. Vectors are cut to `embedding_dims` (256, MRL) and re-normalized.

## Index (`src/files-index.ts`, `data/files-index.sqlite`)
- Roots: `file_roots` (Admin → Ayarlar → Dosyalar, one path per line; default `~/Documents`). Walked breadth-first
  (cap 50,000). Skipped: hidden entries, packages/bundles (`.app`, `.photoslibrary`, …), symlinks, folders named in
  `file_skip_dirs` (Admin; default `node_modules`, `venv`, `.venv`, `__pycache__`, `build`, `dist`, `.build`,
  `target`, `DerivedData`, `Pods`, `runtime`, `models`, `.git`, `Library`) and code repositories (a folder with
  `.git`) unless the folder is itself a root.
- Name catalog (`catalog` table): every walked file, saved right after the walk, no embedding. `file_search` merges
  name matches (case/diacritic-insensitive, `_ - .` separators, partial tokens like invoice numbers; full match
  scores 1) with semantic hits, so a file is findable by name before it is embedded. Unknown types (binaries,
  archives) stay catalog-only.
- Per file: a card (name, path, type, date) plus content chunks: text/Markdown/code/CSV/JSON/HTML directly, PDF via
  `pdftotext` (optional), docx/xlsx/pptx/odt by unzipping their XML; images (downscaled via ffmpeg), audio
  (≤ 4 × 30 s, 16 kHz mono), video (4 frames + 30 s of its audio track). Files over `file_max_mb` and other types:
  card only. Without ffmpeg: JPEG/PNG/GIF/BMP and WAV/MP3 only.
- Incremental (mtime + size), removed files dropped; each file committed on its own, so an aborted pass resumes
  where it stopped. Order: documents (shallow, then newest), then images/audio/video (newest first). First pass
  60 s after start, then every `file_index_interval_ms`; a failed pass is retried after 60 s (doubling up to the
  interval); waits while a user request or task runs. Admin → Durum: indexed / found, running, last run, Reindex.
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
