import { readFile, chmod } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { homedir } from 'node:os';
import { urgentEntry } from '../.pi/extensions/gmail/classify.ts';

export const root = fileURLToPath(new URL('../', import.meta.url));
const scalars = {
  port: 8765, language: 'auto', whisper_threads: 8, whisper_beam_size: 1, whisper_timeout_ms: 120000,
  min_record_seconds: 0.5, max_record_seconds: 300,
  whisper_bin: 'runtime/whisper/whisper-cli',
  whisper_model: 'models/ggml-large-v3-q5_0.bin',
  whisper_server: true, whisper_server_bin: 'runtime/whisper/whisper-server', whisper_url: 'http://127.0.0.1:8092',
  whisper_idle_ms: 600000, whisper_start_timeout_ms: 60000,
  llama_bin: 'runtime/llama/llama-server', llama_model: 'models/gemma-4-12b-it-qat-q4_0.gguf',
  local_url: 'http://127.0.0.1:8091/v1', local_model: 'gemma-4-12b', local_context: 24576, local_reasoning_budget: 64,
  auto_start_local: true, local_start_timeout_ms: 120000, local_timeout_ms: 180000,
  codex_model: '', remote_timeout_ms: 60000, pi_agent_dir: process.env.PI_CODING_AGENT_DIR || path.join(homedir(), '.pi/agent'),
  jev_enabled: true, jev_model: 'jev-latest', jev_timeout_ms: 10000,
  history_chars: 6000, max_tokens: 768, voice: '',
  tts_engine: 'ema', ema_python: 'runtime/ema/bin/python', ema_worker: 'scripts/ema-worker.py',
  ema_cache: 'models/ema', ema_device: 'cpu', ema_speed: 1.0,
  kokoro_python: 'runtime/kokoro/bin/python', kokoro_worker: 'scripts/kokoro-worker.py',
  kokoro_model: 'models/kokoro/kokoro-v1.0.fp16.onnx', kokoro_voices: 'models/kokoro/voices-v1.0.bin',
  kokoro_voice: 'af_heart', kokoro_speed: 1.0, piper_bin: 'runtime/piper/bin/python',
  piper_model_tr: 'models/piper/tr_TR-dfki-medium.onnx',
  piper_model_en: 'models/piper/en_GB-alan-medium.onnx',
  piper_speed_tr: 1.25, piper_speed_en: 1.25,
  voice_tr: 'Yelda (Turkish (Türkiye))', voice_en: 'Daniel',
  language_detector: 'runtime/detect-language',
  reminders_enabled: false, tasks_enabled: false, tasks_list: 'Asistan',
  mail_max_age_days: 30, mail_max_list_results: 25, mail_max_body_chars: 8000,
  gmail_triage_enabled: false, gmail_daily_limit: 500,
  embedding_enabled: false, embedding_bin: 'runtime/llama/llama-server',
  embedding_model: 'models/embeddinggemma-2/embeddinggemma-2-Q8_0.gguf',
  embedding_mmproj: 'models/embeddinggemma-2/mmproj-embeddinggemma-2-Q8_0.gguf',
  embedding_url: 'http://127.0.0.1:8093', embedding_dims: 256, embedding_context: 4096,
  embedding_idle_ms: 600000, embedding_start_timeout_ms: 60000,
  bitwarden_enabled: false, bitwarden_server: 'https://pass.drascom.uk', bitwarden_email: '', bitwarden_idle_lock_min: 15,
  bitwarden_bin: 'runtime/bitwarden/node_modules/@bitwarden/cli/build/bw.js',
  update_check_hour: 9,
  announce_chime: true, announce_snooze_minutes: 30,
  memory_enabled: true, memory_extract: true, memory_chars: 1900, memory_extract_every: 6, memory_idle_ms: 120000,
  file_max_mb: 100, file_max_chunks: 20, file_index_interval_ms: 1800000, file_organize_threshold: 0.75,
  system_prompt: 'Concise personal assistant. Reply in the language of the latest message (Turkish or English); keep technical terms. No emoji. ' +
    'Calendar is not connected. Email is read-only via the Mail tools; never claim to send, move, delete or mark email.'
} as const;
export const MAX_URGENT_SENDERS = 100;
export const MAX_FILE_ROOTS = 10;
export const defaults = { ...scalars, gmail_urgent_senders: Object.freeze([]) as readonly string[],
  file_roots: Object.freeze(['~/Documents']) as readonly string[],
  file_skip_dirs: Object.freeze(['node_modules', 'venv', '.venv', '__pycache__', 'build', 'dist', '.build', 'target', 'DerivedData', 'Pods',
    'runtime', 'models', '.git', 'Library']) as readonly string[] };
export type Config = { -readonly [K in keyof typeof scalars]: typeof scalars[K] extends number ? number :
  typeof scalars[K] extends boolean ? boolean : string } & { gmail_urgent_senders: readonly string[]; file_roots: readonly string[];
  file_skip_dirs: readonly string[] };

/** `~/x` → home directory; other paths must be absolute. */
export const expandHome = (value: string) => value.replace(/^~(?=\/|$)/, homedir());
/** File search roots (Admin): absolute or `~/` paths, never `/` or the home folder itself, no duplicates. */
export function validateFileRoots(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > MAX_FILE_ROOTS) throw Error(`file_roots must be a list of at most ${MAX_FILE_ROOTS}`);
  const result = value.map(item => {
    const text = typeof item === 'string' ? item.trim().replace(/\/+$/, '') : '';
    const full = path.resolve(expandHome(text || '/'));
    if (!text || text.length > 500 || /[\u0000-\u001f\u007f]/.test(text) || !path.isAbsolute(expandHome(text)) ||
        full === path.parse(full).root || full === homedir()) throw Error(`Geçersiz klasör: ${String(item).slice(0, 60)}`);
    return text;
  });
  return [...new Set(result)];
}

/** Bitwarden: HTTPS server URL, plain email (or empty), idle lock 1-240 minutes. */
export function validateVaultSettings(value: { bitwarden_server: string; bitwarden_email: string; bitwarden_idle_lock_min: number }): void {
  let server: URL;
  try { server = new URL(value.bitwarden_server); } catch { throw Error('bitwarden_server must be an https URL'); }
  if (server.protocol !== 'https:' || server.username || server.password || server.search || server.hash) throw Error('bitwarden_server must be an https URL');
  if (value.bitwarden_email && !/^[^\s@"'\\]{1,100}@[^\s@"'\\]{1,150}$/.test(value.bitwarden_email)) throw Error('bitwarden_email is not an email address');
  if (!Number.isInteger(value.bitwarden_idle_lock_min) || value.bitwarden_idle_lock_min < 1 || value.bitwarden_idle_lock_min > 240)
    throw Error('bitwarden_idle_lock_min must be an integer between 1 and 240');
}

export const MAX_SKIP_DIRS = 100;
/** Folder names never walked by the file index (any depth): plain names, no paths, no duplicates. */
export function validateSkipDirs(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > MAX_SKIP_DIRS) throw Error(`file_skip_dirs must be a list of at most ${MAX_SKIP_DIRS}`);
  const result = value.map(item => {
    const text = typeof item === 'string' ? item.trim() : '';
    if (!text || text.length > 100 || /[\/\u0000-\u001f\u007f]/.test(text) || text === '.' || text === '..') throw Error(`Geçersiz: ${String(item).slice(0, 60)}`);
    return text;
  });
  return [...new Set(result)];
}

/** Gmail urgent senders (Admin only): `a@b.c`, `@b.c` or `@*.b.c` (subdomains), normalised, no duplicates. */
export function validateUrgentSenders(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > MAX_URGENT_SENDERS) throw Error(`gmail_urgent_senders must be a list of at most ${MAX_URGENT_SENDERS}`);
  const result = value.map(item => {
    const entry = typeof item === 'string' ? urgentEntry(item) : '';
    if (!entry) throw Error(`Geçersiz: ${String(item).slice(0, 60)}`);
    return entry;
  });
  return [...new Set(result)];
}

/** Old settings accepted and ignored: the pre-multi-account Gmail address (accounts live in the secret store) and the
 *  removed Apple Mail reader's accounts (mail is read through the connected Gmail accounts now), and the removed
 *  active-topic-to-remote switch (the active topic always goes to the remote model now). */
const LEGACY_IGNORED = { gmail_account: 'string', mail_accounts: 'object', mail_enabled: 'boolean', mail_account: 'string', mail_mailbox: 'string', memory_topic_remote: 'boolean' } as const;

export async function loadConfig(configPath = path.join(root, 'config.json')): Promise<Config> {
  let settings: Record<string, unknown> = {};
  try { settings = JSON.parse(await readFile(configPath, 'utf8')); }
  catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e; }
  if (!settings || Array.isArray(settings) || typeof settings !== 'object') throw Error('config.json must contain an object');
  for (const [key, value] of Object.entries(settings)) {
    if (key in LEGACY_IGNORED) {
      if (typeof value !== LEGACY_IGNORED[key as keyof typeof LEGACY_IGNORED]) throw Error(`Invalid setting: ${key}`);
      delete settings[key]; continue;
    }
    if (key === 'gmail_urgent_senders' || key === 'file_roots' || key === 'file_skip_dirs') continue;
    if (!(key in defaults) || typeof value !== typeof defaults[key as keyof typeof defaults]) throw Error(`Invalid setting: ${key}`);
    if (typeof value === 'number' && !Number.isFinite(value)) throw Error(`Invalid number: ${key}`);
  }
  const urgent = 'gmail_urgent_senders' in settings ? validateUrgentSenders(settings.gmail_urgent_senders) : defaults.gmail_urgent_senders;
  const fileRoots = 'file_roots' in settings ? validateFileRoots(settings.file_roots) : defaults.file_roots;
  const skipDirs = 'file_skip_dirs' in settings ? validateSkipDirs(settings.file_skip_dirs) : defaults.file_skip_dirs;
  const config: Config = { ...defaults, ...settings, gmail_urgent_senders: urgent, file_roots: fileRoots,
    file_skip_dirs: skipDirs } as Config;
  for (const key of ['whisper_bin', 'whisper_model', 'whisper_server_bin', 'llama_bin', 'llama_model', 'language_detector', 'embedding_bin', 'embedding_model', 'bitwarden_bin',
    'ema_python', 'ema_worker', 'ema_cache', 'kokoro_python', 'kokoro_worker', 'kokoro_model',
    'kokoro_voices', 'piper_bin', 'piper_model_tr', 'piper_model_en'] as const)
    config[key] = path.resolve(root, config[key]);
  if (config.embedding_mmproj) config.embedding_mmproj = path.resolve(root, config.embedding_mmproj); // Empty: text only.
  config.pi_agent_dir = path.resolve(root, config.pi_agent_dir.replace(/^~(?=\/|$)/, homedir()));
  if (!['ema', 'piper', 'say'].includes(config.tts_engine)) throw Error('tts_engine must be ema, piper or say');
  if (!['cpu', 'auto', 'cuda', 'mps'].includes(config.ema_device)) throw Error('ema_device must be cpu, auto, cuda or mps');
  if (config.ema_speed < 0.25 || config.ema_speed > 4) throw Error('ema_speed must be between 0.25 and 4');
  if (config.kokoro_speed < 0.5 || config.kokoro_speed > 2) throw Error('kokoro_speed must be between 0.5 and 2');
  if (!config.kokoro_voice.trim()) throw Error('kokoro_voice must not be empty');
  if (!Number.isInteger(config.mail_max_age_days) || config.mail_max_age_days < 1 || config.mail_max_age_days > 30)
    throw Error('mail_max_age_days must be an integer between 1 and 30');
  if (!Number.isInteger(config.mail_max_list_results) || config.mail_max_list_results < 1 || config.mail_max_list_results > 25)
    throw Error('mail_max_list_results must be an integer between 1 and 25');
  if (!Number.isInteger(config.mail_max_body_chars) || config.mail_max_body_chars < 500 || config.mail_max_body_chars > 8000)
    throw Error('mail_max_body_chars must be an integer between 500 and 8000');
  if (!config.tasks_list.trim() || config.tasks_list.length > 100 || /[\u0000-\u001f\u007f]/.test(config.tasks_list))
    throw Error('tasks_list must be a Reminders list name (1-100 characters)');
  if (!Number.isInteger(config.gmail_daily_limit) || config.gmail_daily_limit > 5000)
    throw Error('gmail_daily_limit must be an integer between 1 and 5000');
  const local = new URL(config.local_url);
  if (local.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(local.hostname) ||
      !local.port || local.pathname.replace(/\/$/, '') !== '/v1' || local.username || local.search || local.hash)
    throw Error('local_url must be a loopback HTTP URL ending in /v1');
  if (!Number.isInteger(config.port) || config.port < 1 || config.port > 65535 || config.port === Number(local.port))
    throw Error('Invalid UI port');
  const whisper = new URL(config.whisper_url);
  if (whisper.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(whisper.hostname) || !whisper.port ||
      whisper.pathname !== '/' || whisper.username || whisper.search || whisper.hash ||
      [config.port, Number(local.port)].includes(Number(whisper.port)))
    throw Error('whisper_url must be a loopback HTTP URL with its own port');
  const embed = new URL(config.embedding_url);
  if (embed.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(embed.hostname) || !embed.port ||
      embed.pathname !== '/' || embed.username || embed.search || embed.hash ||
      [config.port, Number(local.port), Number(whisper.port)].includes(Number(embed.port)))
    throw Error('embedding_url must be a loopback HTTP URL with its own port');
  if (![768, 512, 256, 128].includes(config.embedding_dims)) throw Error('embedding_dims must be 768, 512, 256 or 128');
  if (!Number.isInteger(config.embedding_context) || config.embedding_context < 1024 || config.embedding_context > 8192)
    throw Error('embedding_context must be an integer between 1024 and 8192');
  if (!Number.isInteger(config.file_max_chunks) || config.file_max_chunks > 200) throw Error('file_max_chunks must be an integer between 1 and 200');
  validateVaultSettings(config);
  if (config.file_organize_threshold >= 1) throw Error('file_organize_threshold must be between 0 and 1');
  for (const [key, value] of Object.entries(config))
    if (typeof value === 'number' && !key.startsWith('min_') && key !== 'local_reasoning_budget' && key !== 'update_check_hour' && value <= 0) throw Error(`${key} must be positive`);
  if (!Number.isInteger(config.update_check_hour) || config.update_check_hour < 0 || config.update_check_hour > 23)
    throw Error('update_check_hour must be an integer between 0 and 23');
  if (!Number.isInteger(config.announce_snooze_minutes) || config.announce_snooze_minutes > 1440) throw Error('announce_snooze_minutes must be an integer between 1 and 1440');
  if (!Number.isInteger(config.memory_extract_every) || config.memory_extract_every > 50) throw Error('memory_extract_every must be an integer between 1 and 50');
  if (config.memory_chars > 4000) throw Error('memory_chars must be between 1 and 4000');
  if (!Number.isInteger(config.whisper_beam_size) || config.whisper_beam_size > 16)
    throw Error('whisper_beam_size must be an integer between 1 (greedy) and 16');
  if (!Number.isInteger(config.local_reasoning_budget) || config.local_reasoning_budget < -1 || config.local_reasoning_budget > 4096)
    throw Error('local_reasoning_budget must be an integer between -1 and 4096');
  if (config.min_record_seconds < 0 || config.min_record_seconds >= config.max_record_seconds ||
      config.local_context < 2048 || config.max_tokens >= config.local_context / 2) throw Error('Invalid recording/context limits');
  return config;
}

export async function loadEnv(): Promise<void> {
  const allowed = new Set(['TYPESAFE_AI_API_TOKEN', 'TYPESAFE_API_KEY', 'ASSISTANT_LOCAL_API_KEY']);
  for (const name of ['.env', 'env.txt']) {
    const file = path.join(root, name);
    let text: string;
    try { text = await readFile(file, 'utf8'); await chmod(file, 0o600); }
    catch (e) { if ((e as NodeJS.ErrnoException).code === 'ENOENT') continue; throw e; }
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim() || line.trim().startsWith('#')) continue;
      const match = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
      if (!match || !allowed.has(match[1])) throw Error(`Unsupported setting in ${name}`);
      let value = match[2];
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
      process.env[match[1]] ??= value;
    }
  }
  process.env.TYPESAFE_API_KEY ??= process.env.TYPESAFE_AI_API_TOKEN;
}
