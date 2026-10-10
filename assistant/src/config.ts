import { readFile, chmod } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { homedir } from 'node:os';

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
  mail_max_age_days: 30, mail_max_list_results: 25, mail_max_body_chars: 8000,
  system_prompt: 'Concise personal assistant. Reply in the language of the latest message (Turkish or English); keep technical terms. No emoji. ' +
    'Calendar is not connected. Email is read-only via the Mail tools; never claim to send, move, delete or mark email.'
} as const;
/** One Apple Mail account/mailbox the user enabled in Admin; the model never chooses these. */
export type MailAccount = { readonly account: string; readonly mailbox: string; readonly enabled: boolean };
export const MAX_MAIL_ACCOUNTS = 10;
export const defaults = { ...scalars, mail_accounts: Object.freeze([]) as readonly MailAccount[] };
export type Config = { -readonly [K in keyof typeof scalars]: typeof scalars[K] extends number ? number :
  typeof scalars[K] extends boolean ? boolean : string } & { mail_accounts: readonly MailAccount[] };

const mailName = (value: unknown): value is string =>
  typeof value === 'string' && Boolean(value.trim()) && value.length <= 200 && !/[\u0000-\u001f\u007f]/.test(value);
/** Validates a full mail account list (Admin save and config load): names, size, duplicates. */
export function validateMailAccounts(value: unknown): MailAccount[] {
  if (!Array.isArray(value) || value.length > MAX_MAIL_ACCOUNTS) throw Error(`mail_accounts must be a list of at most ${MAX_MAIL_ACCOUNTS}`);
  const seen = new Set<string>();
  return value.map(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).some(k => !['account', 'mailbox', 'enabled'].includes(k)))
      throw Error('Invalid mail account entry');
    const { account, mailbox = 'INBOX', enabled = true } = item as Record<string, unknown>;
    if (!mailName(account) || !mailName(mailbox) || typeof enabled !== 'boolean') throw Error('Invalid mail account or mailbox');
    const key = JSON.stringify([account, mailbox.toLowerCase()]);
    if (seen.has(key)) throw Error('Duplicate mail account and mailbox');
    seen.add(key);
    return Object.freeze({ account, mailbox, enabled });
  });
}
/** Pre-list config: mail_enabled/mail_account/mail_mailbox become one list entry. */
const LEGACY_MAIL = { mail_enabled: 'boolean', mail_account: 'string', mail_mailbox: 'string' } as const;

export async function loadConfig(configPath = path.join(root, 'config.json')): Promise<Config> {
  let settings: Record<string, unknown> = {};
  try { settings = JSON.parse(await readFile(configPath, 'utf8')); }
  catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e; }
  if (!settings || Array.isArray(settings) || typeof settings !== 'object') throw Error('config.json must contain an object');
  const legacy: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(settings)) {
    if (key in LEGACY_MAIL) {
      if (typeof value !== LEGACY_MAIL[key as keyof typeof LEGACY_MAIL]) throw Error(`Invalid setting: ${key}`);
      legacy[key] = value; delete settings[key]; continue;
    }
    if (key === 'mail_accounts') continue;
    if (!(key in defaults) || typeof value !== typeof defaults[key as keyof typeof defaults]) throw Error(`Invalid setting: ${key}`);
    if (typeof value === 'number' && !Number.isFinite(value)) throw Error(`Invalid number: ${key}`);
  }
  let mailAccounts: readonly MailAccount[] = defaults.mail_accounts;
  if ('mail_accounts' in settings) mailAccounts = validateMailAccounts(settings.mail_accounts);
  else if (Object.keys(legacy).length) {
    const account = legacy.mail_account as string | undefined ?? '';
    if (legacy.mail_enabled === true && !account.trim()) throw Error('mail_enabled requires mail_account');
    if (account.trim()) mailAccounts = validateMailAccounts([{ account, mailbox: legacy.mail_mailbox ?? 'INBOX', enabled: legacy.mail_enabled === true }]);
    else if (legacy.mail_mailbox !== undefined && !mailName(legacy.mail_mailbox)) throw Error('Invalid mail account or mailbox');
  }
  const config: Config = { ...defaults, ...settings, mail_accounts: mailAccounts } as Config;
  for (const key of ['whisper_bin', 'whisper_model', 'whisper_server_bin', 'llama_bin', 'llama_model', 'language_detector',
    'ema_python', 'ema_worker', 'ema_cache', 'kokoro_python', 'kokoro_worker', 'kokoro_model',
    'kokoro_voices', 'piper_bin', 'piper_model_tr', 'piper_model_en'] as const)
    config[key] = path.resolve(root, config[key]);
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
  for (const [key, value] of Object.entries(config))
    if (typeof value === 'number' && !key.startsWith('min_') && key !== 'local_reasoning_budget' && value <= 0) throw Error(`${key} must be positive`);
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
