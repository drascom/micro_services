import { loadSkillsFromDir } from '@earendil-works/pi-coding-agent';
import { lstat, mkdir, mkdtemp, open, readdir, readFile, realpath, rename, rm, stat, writeFile } from 'node:fs/promises';
import lockfile from 'proper-lockfile';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { loadConfig, validateUrgentSenders, type Config } from './config.ts';
import type { MemoryService } from './memory.ts';
import type { GmailService } from '../.pi/extensions/gmail/service.ts';
import { lastPlan, MailTriage, triageMemory } from '../.pi/extensions/gmail/triage.ts';
import type { Orchestrator } from './orchestrator.ts';
import { FileCredentials } from './credentials.ts';
import { liveRequests, recentActivity } from './activity.ts';
import { runProcess, type OwnedProcess } from './processes.ts';
import { PiChat, remoteError, subscriptionProvider } from './providers.ts';
import type { Models } from '@earendil-works/pi-ai';
import type { FileService } from './files.ts';
import { lastFilePlan } from './files-organize.ts';
import { validateFileRoots, validateSkipDirs, validateVaultSettings } from './config.ts';
import { VaultError, type VaultService } from './vault.ts';

/** Native admin window data. User actions only: nothing here is sent to or callable by the model. */

const started = Date.now();
async function memory(processes: OwnedProcess[]): Promise<Map<number, number>> {
  const pids = [process.pid, ...processes.flatMap(p => p.pid ? [p.pid] : [])];
  const result = new Map<number, number>();
  try {
    const output = await runProcess('/bin/ps', ['-o', 'pid=,rss=', '-p', pids.join(',')], AbortSignal.timeout(2000));
    for (const line of output.trim().split('\n')) {
      const [pid, rss] = line.trim().split(/\s+/).map(Number);
      if (pid && Number.isFinite(rss)) result.set(pid, Math.round(rss / 1024));
    }
  } catch {}
  return result;
}

export async function collectStatus(config: Config, app: Orchestrator, state: { busy: boolean; unloading: boolean }, activityFile: string | null, files?: FileService) {
  const owned = [...(app.runtime.processes?.() ?? []), ...(app.speech.processes?.() ?? []), ...(config.embedding_enabled ? files?.processes() ?? [] : [])];
  const [rss, localHealthy, credentials, remoteModel, activity] = await Promise.all([
    memory(owned),
    app.runtime.healthy?.(AbortSignal.timeout(1500)) ?? Promise.resolve(false),
    new FileCredentials(path.join(config.pi_agent_dir, 'auth.json')).list().catch(() => []),
    app.remote instanceof PiChat ? app.remote.resolveModel().then(m => `${m.provider}/${m.id}`, (e: Error) => `unavailable: ${e.message}`) : Promise.resolve('injected'),
    activityFile ? recentActivity(activityFile, 100).catch(() => []) : Promise.resolve([])
  ]);
  return {
    backend: { pid: process.pid, port: config.port, uptime_s: Math.round((Date.now() - started) / 1000), memory_mb: rss.get(process.pid) },
    busy: state.busy, unloading: state.unloading, requests: liveRequests(),
    remote_model: remoteModel, jev_enabled: config.jev_enabled && Boolean(app.classifier), tts_engine: config.tts_engine,
    // Provider and credential type only; never tokens.
    pi_login: credentials.map(c => ({ provider: c.providerId, type: c.type })),
    local_healthy: localHealthy,
    processes: owned.map(p => ({ ...p, ...(p.pid && rss.has(p.pid) ? { memory_mb: rss.get(p.pid) } : {}) })),
    history_messages: app.history.length,
    activity
  };
}

export type ResourceKind = 'skill' | 'extension';
const NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SEGMENT = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;
const MAX_FILE = 256 * 1024;
const FACTORY = new Set(['permissions.ts', 'request-workflow.ts', 'mail', 'apple-reminders', 'gmail', 'files', 'vault', 'updates']);

function base(root: string, kind: unknown): string {
  if (kind !== 'skill' && kind !== 'extension') throw Error('Invalid resource kind');
  return path.join(root, '.pi', kind === 'skill' ? 'skills' : 'extensions');
}

/** Resolve a relative file path strictly inside the resource root: safe segments, no symlinks anywhere on the path. */
async function resolveFile(root: string, kind: unknown, relative: unknown, mustExist = true): Promise<string> {
  const directory = base(root, kind);
  if (typeof relative !== 'string' || !relative || relative.length > 300) throw Error('Invalid path');
  const segments = relative.split('/');
  if (segments.some(s => !SEGMENT.test(s) || s === '..' || s === '.')) throw Error('Invalid path');
  const realBase = await realpath(directory);
  let target = realBase;
  for (const [index, segment] of segments.entries()) {
    target = path.join(target, segment);
    try {
      const info = await lstat(target);
      if (info.isSymbolicLink()) throw Error('Symbolic links are not allowed');
      if (index === segments.length - 1 ? !info.isFile() : !info.isDirectory()) throw Error('Invalid path');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') { if (mustExist || index < segments.length - 1) throw Error('Dosya bulunamadı'); }
      else throw error;
    }
  }
  if (!target.startsWith(realBase + path.sep)) throw Error('Path outside resource directory');
  return target;
}

async function files(directory: string, prefix: string, depth = 0): Promise<string[]> {
  const result: string[] = [];
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!SEGMENT.test(entry.name) || entry.isSymbolicLink()) continue;
    if (entry.isFile()) result.push(prefix + entry.name);
    else if (entry.isDirectory() && depth < 3) result.push(...await files(path.join(directory, entry.name), prefix + entry.name + '/', depth + 1));
    if (result.length > 200) break;
  }
  return result;
}

function extensionDescription(source: string): string {
  const match = source.match(/\/\*\*\s*([\s\S]*?)\*\//) || source.match(/^\s*\/\/\s*(.+)$/m);
  return match ? match[1].replace(/\s*\*\s*/g, ' ').trim().slice(0, 200) : '';
}

export async function listResources(root: string) {
  const skillsDir = base(root, 'skill'), extensionsDir = base(root, 'extension');
  const skills = [];
  for (const entry of await readdir(skillsDir, { withFileTypes: true }).catch(() => [])) {
    if (!entry.isDirectory() || !SEGMENT.test(entry.name)) continue;
    const directory = path.join(skillsDir, entry.name);
    const parsed = loadSkillsFromDir({ dir: directory, source: 'ayhan' });
    skills.push({ kind: 'skill', name: entry.name, description: parsed.skills[0]?.description ?? '',
      path: `.pi/skills/${entry.name}`, files: await files(directory, entry.name + '/'),
      valid: parsed.skills.some(s => s.name === entry.name) && !parsed.diagnostics.length });
  }
  const extensions = [];
  for (const entry of await readdir(extensionsDir, { withFileTypes: true }).catch(() => [])) {
    if (!SEGMENT.test(entry.name) || entry.isSymbolicLink()) continue;
    if (!entry.isDirectory() && !/\.(ts|js)$/.test(entry.name)) continue;
    const list = entry.isDirectory() ? await files(path.join(extensionsDir, entry.name), entry.name + '/') : [entry.name];
    const main = list.find(f => /(^|\/)index\.(ts|js)$/.test(f)) ?? list[0];
    const source = main ? await readFile(path.join(extensionsDir, main), 'utf8').catch(() => '') : '';
    extensions.push({ kind: 'extension', name: entry.name, description: extensionDescription(source),
      path: `.pi/extensions/${entry.name}`, files: list, loading: FACTORY.has(entry.name) ? 'built-in' : 'auto' });
  }
  return { skills, extensions };
}

export async function readResource(root: string, kind: unknown, relative: unknown) {
  const file = await resolveFile(root, kind, relative);
  const handle = await open(file, 'r');
  try {
    if ((await handle.stat()).size > MAX_FILE) throw Error('Dosya burada düzenlemek için çok büyük');
    return { kind, path: relative, content: await handle.readFile('utf8') };
  } finally { await handle.close(); }
}

/** Validate SKILL.md with the harness parser in a scratch copy, so an invalid skill is never saved. */
async function validateSkill(name: string, content: string): Promise<void> {
  const scratch = await mkdtemp(path.join(tmpdir(), 'ayhan-skill-'));
  try {
    const directory = path.join(scratch, name);
    await mkdir(directory); await writeFile(path.join(directory, 'SKILL.md'), content);
    const result = loadSkillsFromDir({ dir: directory, source: 'ayhan' });
    const problems = result.diagnostics.map(d => d.message);
    if (!result.skills.some(s => s.name === name)) problems.unshift(`Skill name must be "${name}" with a description`);
    if (problems.length) throw Error('Skill doğrulanamadı: ' + problems.join('; '));
  } finally { await rm(scratch, { recursive: true, force: true }); }
}

async function atomicWrite(file: string, content: string, exclusive = false): Promise<void> {
  if (exclusive) {
    try { await writeFile(file, content, { flag: 'wx', mode: 0o644 }); return; }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'EEXIST') throw Error('Zaten var'); throw error; }
  }
  const temporary = `${file}.${process.pid}.admin.tmp`;
  await writeFile(temporary, content, { mode: 0o644 });
  await rename(temporary, file);
}

export async function writeResource(root: string, kind: unknown, relative: unknown, content: unknown) {
  if (typeof content !== 'string' || Buffer.byteLength(content) > MAX_FILE) throw Error('Invalid content');
  const file = await resolveFile(root, kind, relative);
  const segments = (relative as string).split('/');
  if (kind === 'skill' && segments.length === 2 && segments[1] === 'SKILL.md') await validateSkill(segments[0], content);
  await atomicWrite(file, content);
  return { ok: true, note: 'Kaydedildi.' };
}

export async function createSkill(root: string, name: unknown, description: unknown) {
  if (typeof name !== 'string' || name.length > 64 || !NAME.test(name)) throw Error('Ad: küçük harf, rakam ve tire');
  if (typeof description !== 'string' || !description.trim() || description.length > 300 || /[\r\n]/.test(description)) throw Error('Açıklama: tek satır, en çok 300 karakter');
  const content = `---\nname: ${name}\ndescription: ${JSON.stringify(description.trim())}\n---\n# ${name}\n\nDescribe the workflow here. Keep it short.\n`;
  await validateSkill(name, content);
  const directory = path.join(await realpath(base(root, 'skill')), name);
  try { await mkdir(directory); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'EEXIST') throw Error('Zaten var'); throw error; }
  await atomicWrite(path.join(directory, 'SKILL.md'), content, true);
  return { ok: true, path: `${name}/SKILL.md`, note: 'Oluşturuldu.' };
}

/** Move a whole skill or extension out of the resource root into a trash directory (first that works); never a hard delete. */
export async function deleteResource(root: string, kind: unknown, name: unknown, trashDirs: string[]) {
  const directory = base(root, kind);
  if (typeof name !== 'string' || !SEGMENT.test(name) || name.length > 100) throw Error('Invalid name');
  if (kind === 'extension' && FACTORY.has(name)) throw Error('Yerleşik extension silinemez');
  const realBase = await realpath(directory);
  const target = path.join(realBase, name);
  if (path.dirname(target) !== realBase) throw Error('Path outside resource directory');
  let info;
  try { info = await lstat(target); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw Error('Not found'); throw error; }
  if (info.isSymbolicLink()) throw Error('Symbolic links are not allowed');
  if (kind === 'skill' ? !info.isDirectory() : !(info.isDirectory() || (info.isFile() && /\.(ts|js)$/.test(name)))) throw Error('Invalid resource');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const extension = info.isFile() ? path.extname(name) : '';
  const trashed = `${path.basename(name, extension)}-${stamp}${extension}`;
  let lastError: unknown = Error('No trash directory available');
  for (const trash of trashDirs) {
    try {
      await mkdir(trash, { recursive: true, mode: 0o700 });
      const destination = path.join(trash, trashed);
      await rename(target, destination);
      return { ok: true, trashed: destination, note: `Çöp kutusuna taşındı: ${destination}` };
    } catch (error) { lastError = error; }
  }
  throw lastError;
}

export async function createExtension(root: string, name: unknown) {
  if (typeof name !== 'string' || name.length > 64 || !NAME.test(name)) throw Error('Ad: küçük harf, rakam ve tire');
  const content = `import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';\n\n/** ${name}: describe what this extension does. */\nexport default function ${name.replace(/-([a-z0-9])/g, (_, c: string) => c.toUpperCase()).replace(/^[0-9]/, '_$&')}(_pi: ExtensionAPI) {\n  // Register tools with pi.registerTool(); new tools stay unavailable until approved in the harness.\n}\n`;
  await atomicWrite(path.join(await realpath(base(root, 'extension')), `${name}.ts`), content, true);
  return { ok: true, path: `${name}.ts`, note: 'Oluşturuldu.' };
}

/** Changes config.json under a lock and atomically; the whole file must still load. Other settings are kept. */
async function updateConfigFile(configPath: string, change: (settings: Record<string, unknown>) => void) {
  const release = await lockfile.lock(configPath, { realpath: false, stale: 10000, retries: { retries: 20, minTimeout: 50, maxTimeout: 250 } });
  try {
    let settings: Record<string, unknown> = {}, mode = 0o644;
    try {
      const text = await readFile(configPath, 'utf8');
      mode = (await stat(configPath)).mode & 0o777;
      try { settings = JSON.parse(text); } catch { throw Error('config.json is not valid JSON'); }
      if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw Error('config.json must contain an object');
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    change(settings);
    for (const key of ['mail_accounts', 'mail_enabled', 'mail_account', 'mail_mailbox']) delete settings[key]; // Removed Apple Mail reader.
    const temporary = `${configPath}.${process.pid}.admin.tmp`;
    try {
      await writeFile(temporary, JSON.stringify(settings, null, 2) + '\n', { mode });
      await loadConfig(temporary); // The whole file must still load before it replaces config.json.
      await rename(temporary, configPath);
    } catch (error) { await rm(temporary, { force: true }); throw error; }
  } finally { await release(); }
}

/**
 * Reminders on/off (off: the `reminders` tool is not registered for the model at all, from the next request) and
 * assistant tasks (on/off + list name). Only the fields present are changed.
 */
export async function saveReminders(config: Config, configPath: string, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { enabled?: unknown; tasks_enabled?: unknown; tasks_list?: unknown };
  const change: Partial<Pick<Config, 'reminders_enabled' | 'tasks_enabled' | 'tasks_list'>> = {};
  if (input.enabled !== undefined) { if (typeof input.enabled !== 'boolean') throw Error('enabled must be a boolean'); change.reminders_enabled = input.enabled; }
  if (input.tasks_enabled !== undefined) { if (typeof input.tasks_enabled !== 'boolean') throw Error('tasks_enabled must be a boolean'); change.tasks_enabled = input.tasks_enabled; }
  if (input.tasks_list !== undefined) { if (typeof input.tasks_list !== 'string') throw Error('tasks_list must be text'); change.tasks_list = input.tasks_list.trim(); }
  if (!Object.keys(change).length) throw Error('Kaydedilecek bir şey yok');
  await updateConfigFile(configPath, settings => { Object.assign(settings, change); }); // The whole file is validated before replacing.
  Object.assign(config, change);
  return { enabled: config.reminders_enabled, tasks_enabled: config.tasks_enabled, tasks_list: config.tasks_list };
}

/** Memory tab: both switches and every stored item/summary (local Admin only; never logged). */
export async function memoryStatus(config: Config, memory: MemoryService) {
  const { items, episodes } = await memory.store.list();
  return { enabled: config.memory_enabled, extract: config.memory_extract, items, episodes };
}
/** Memory switches in config.json; only the fields present are changed. */
export async function saveMemorySettings(config: Config, configPath: string, memory: MemoryService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { enabled?: unknown; extract?: unknown };
  const change: Partial<Pick<Config, 'memory_enabled' | 'memory_extract'>> = {};
  if (input.enabled !== undefined) { if (typeof input.enabled !== 'boolean') throw Error('enabled must be a boolean'); change.memory_enabled = input.enabled; }
  if (input.extract !== undefined) { if (typeof input.extract !== 'boolean') throw Error('extract must be a boolean'); change.memory_extract = input.extract; }
  if (!Object.keys(change).length) throw Error('Kaydedilecek bir şey yok');
  await updateConfigFile(configPath, settings => { Object.assign(settings, change); });
  Object.assign(config, change);
  if (!config.memory_enabled || !config.memory_extract) memory.extractor.turns = [];
  return memoryStatus(config, memory);
}
/** Add (no id), edit text and/or pin (id). The privacy filter applies to the user's own edits too. */
export async function saveMemoryItem(config: Config, memory: MemoryService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { id?: unknown; kind?: unknown; text?: unknown; pinned?: unknown };
  if (input.text !== undefined && typeof input.text !== 'string') throw Error('text must be text');
  if (input.pinned !== undefined && typeof input.pinned !== 'boolean') throw Error('pinned must be a boolean');
  const signal = AbortSignal.timeout(30_000);
  const result = input.id !== undefined
    ? Number.isInteger(input.id) ? await memory.store.edit(input.id as number, { text: input.text as string | undefined, pinned: input.pinned as boolean | undefined }, signal) : undefined
    : input.kind === 'assistant' || input.kind === 'personal'
      ? await memory.store.save({ kind: input.kind, text: String(input.text ?? ''), source: 'explicit', pinned: input.pinned === true }, signal) : undefined;
  if (!result) throw Error('Invalid memory item');
  if (result.status === 'rejected') throw Error(result.reason === 'private' ? 'Şifre, kod, hesap numarası veya mail içeriği saklanmaz'
    : result.reason === 'not_found' ? 'Kayıt yok' : 'Boş metin');
  return memoryStatus(config, memory);
}
/** Delete one item (`id`), one summary (`episode`) or all of a kind (`all`: assistant | personal | episodes). */
export async function deleteMemory(config: Config, memory: MemoryService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { id?: unknown; episode?: unknown; all?: unknown };
  if (Number.isInteger(input.id)) await memory.store.remove(input.id as number);
  else if (Number.isInteger(input.episode)) await memory.store.removeEpisode(input.episode as number);
  else if (input.all === 'assistant' || input.all === 'personal' || input.all === 'episodes') await memory.store.clear(input.all);
  else throw Error('Invalid memory delete');
  return memoryStatus(config, memory);
}

/** Gmail: connected accounts (each with its missing labels and error), shared triage settings; never secrets or tokens. */
export async function gmailStatus(config: Config, service: GmailService) {
  const auth = await service.auth.status();
  const accounts = await Promise.all(auth.accounts.map(async a => ({ ...a, missing_labels: a.error ? [] : await service.missingLabels(a.email) })));
  return { ...auth, accounts, triage_enabled: config.gmail_triage_enabled,
    urgent_senders: config.gmail_urgent_senders, daily_limit: config.gmail_daily_limit, error: service.auth.lastError };
}

/**
 * Gmail settings: OAuth client (Keychain only, never config.json), triage switch and urgent senders (config.json, shared
 * by all accounts). Only the fields present are changed.
 */
export async function saveGmail(config: Config, configPath: string, service: GmailService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  if (input.client_id !== undefined || input.client_secret !== undefined) await service.auth.saveClient(input.client_id, input.client_secret);
  const change: Partial<Pick<Config, 'gmail_triage_enabled' | 'gmail_urgent_senders'>> = {};
  if (input.triage_enabled !== undefined) { if (typeof input.triage_enabled !== 'boolean') throw Error('triage_enabled must be a boolean'); change.gmail_triage_enabled = input.triage_enabled; }
  if (input.urgent_senders !== undefined) change.gmail_urgent_senders = validateUrgentSenders(input.urgent_senders);
  if (Object.keys(change).length) {
    await updateConfigFile(configPath, settings => { Object.assign(settings, change); delete settings.gmail_account; });
    Object.assign(config, change);
  }
  return gmailStatus(config, service);
}

/** Adds an account (or renews one): starts the browser consent for any Google account; the Mac app opens `auth_url`. */
export async function connectGmail(service: GmailService) {
  const { auth_url, done } = await service.auth.connect();
  done.then(email => { service.forgetLabels(); return service.ensureLabels(email); }).catch(() => {});
  return { auth_url };
}

export async function disconnectGmail(config: Config, service: GmailService, value: unknown) {
  const account = (value && typeof value === 'object' ? (value as { account?: unknown }).account : undefined);
  if (typeof account !== 'string' || !account.trim()) throw Error('account is required');
  await service.auth.disconnect(account); service.forgetLabels();
  return gmailStatus(config, service);
}

const adminTriage = (config: Config, service: GmailService) => new MailTriage({ mailboxes: service, settings: config, memory: triageMemory,
  turn: 0, logFile: service.logFile, stateFile: service.stateFile, importance: () => service.importance() });
export async function gmailActions(config: Config, service: GmailService) {
  return { batches: await adminTriage(config, service).recent(10), last_plan: lastPlan() ?? null };
}
export async function undoGmail(config: Config, service: GmailService) {
  const result = await adminTriage(config, service).undo(AbortSignal.timeout(60_000));
  return { ...result, batches: await adminTriage(config, service).recent(10) };
}

/** Bitwarden: switch, server, email, CLI state; never the password, session key or any vault item. */
export async function vaultStatus(config: Config, vault: VaultService) {
  const installed = await vault.installed();
  let state = 'unknown', cli_server = '', error = vault.lastError;
  if (installed) {
    try { ({ state, server: cli_server } = await vault.status()); }
    catch (e) { error = e instanceof VaultError ? e.code : 'vault_failed'; }
  }
  return { enabled: config.bitwarden_enabled, server: config.bitwarden_server, email: config.bitwarden_email,
    idle_lock_min: config.bitwarden_idle_lock_min, installed, state, cli_server, error };
}
/** Only the fields present are changed. Off: the vault is locked and its tools are not registered. */
export async function saveVault(config: Config, configPath: string, vault: VaultService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { enabled?: unknown; email?: unknown; server?: unknown; idle_lock_min?: unknown };
  const change: Partial<Pick<Config, 'bitwarden_enabled' | 'bitwarden_email' | 'bitwarden_server' | 'bitwarden_idle_lock_min'>> = {};
  if (input.enabled !== undefined) { if (typeof input.enabled !== 'boolean') throw Error('enabled must be a boolean'); change.bitwarden_enabled = input.enabled; }
  if (input.email !== undefined) { if (typeof input.email !== 'string') throw Error('email must be text'); change.bitwarden_email = input.email.trim(); }
  if (input.server !== undefined) { if (typeof input.server !== 'string') throw Error('server must be text'); change.bitwarden_server = input.server.trim().replace(/\/+$/, ''); }
  if (input.idle_lock_min !== undefined) { if (typeof input.idle_lock_min !== 'number') throw Error('idle_lock_min must be a number'); change.bitwarden_idle_lock_min = input.idle_lock_min; }
  if (!Object.keys(change).length) throw Error('Kaydedilecek bir şey yok');
  validateVaultSettings({ ...config, ...change });
  await updateConfigFile(configPath, settings => { Object.assign(settings, change); });
  Object.assign(config, change);
  if (!config.bitwarden_enabled) await vault.lock();
  return vaultStatus(config, vault);
}
/** Admin unlock (logs in first if needed). The password is used once and never stored, logged or returned. */
export async function unlockVault(config: Config, vault: VaultService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { password?: unknown; code?: unknown };
  if (!config.bitwarden_enabled) throw new VaultError('disabled');
  if (!await vault.installed()) throw new VaultError('not_installed');
  const { state, server } = await vault.status();
  if (state === 'unauthenticated' && server.replace(/\/+$/, '') !== config.bitwarden_server) await vault.setServer(config.bitwarden_server);
  try { await vault.unlock(input.password, input.code); }
  catch (error) { throw error instanceof VaultError ? error : new VaultError('vault_failed'); }
  return vaultStatus(config, vault);
}

/** Local files (EmbeddingGemma index): switch, folders, index counts and the latest organize plan; never file contents. */
export async function filesStatus(config: Config, files: FileService) {
  return { enabled: config.embedding_enabled, roots: config.file_roots, skip_dirs: config.file_skip_dirs, index: config.embedding_enabled ? await files.index.status() : null,
    last_plan: lastFilePlan() ?? null };
}
/** Only the fields present are changed. Off: the file tools are not registered and the embedding server stops. */
export async function saveFiles(config: Config, configPath: string, files: FileService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as { enabled?: unknown; roots?: unknown; skip_dirs?: unknown };
  const change: Partial<Pick<Config, 'embedding_enabled' | 'file_roots' | 'file_skip_dirs'>> = {};
  if (input.enabled !== undefined) { if (typeof input.enabled !== 'boolean') throw Error('enabled must be a boolean'); change.embedding_enabled = input.enabled; }
  if (input.roots !== undefined) change.file_roots = validateFileRoots(input.roots);
  if (input.skip_dirs !== undefined) change.file_skip_dirs = validateSkipDirs(input.skip_dirs);
  if (!Object.keys(change).length) throw Error('Kaydedilecek bir şey yok');
  await updateConfigFile(configPath, settings => { Object.assign(settings, change); });
  Object.assign(config, change);
  if (!config.embedding_enabled) await files.embedder.close();
  return filesStatus(config, files);
}

/** Remote model picker: the signed-in subscription provider's models from the Pi registry. `model: ""` = follow global Pi. */
export async function remoteModels(config: Config, models: Pick<Models, 'getModels'> | undefined) {
  let provider = '', list: string[] = [], error = '';
  try { provider = await subscriptionProvider(config); list = models ? models.getModels(provider).map(m => m.id) : []; }
  catch (e) { error = (e as Error).message; }
  return { model: config.codex_model, provider, models: list, error };
}
/** Saves `codex_model` (empty = global Pi choice); takes effect on the next request (resolveModel reads the live config). */
export async function saveRemoteModel(config: Config, configPath: string, models: Pick<Models, 'getModels'> | undefined, value: unknown) {
  const model = (value && typeof value === 'object' ? (value as { model?: unknown }).model : undefined);
  if (typeof model !== 'string') throw Error('model must be text');
  if (model) {
    const available = await remoteModels(config, models);
    if (!available.models.includes(model)) throw Error('Model, giriş yapılan sağlayıcıda yok');
  }
  await updateConfigFile(configPath, settings => { settings.codex_model = model; });
  config.codex_model = model;
  return remoteModels(config, models);
}
/** "Dene": one tiny request with the selected model (empty = global Pi choice). Returns ✓ or a short sanitized error; nothing cached. */
export async function testRemoteModel(config: Config, models: Models | undefined, value: unknown) {
  const model = (value && typeof value === 'object' ? (value as { model?: unknown }).model : undefined);
  if (typeof model !== 'string') throw Error('model must be text');
  if (!models) return { ok: false, error: 'Remote model unavailable' };
  try {
    const resolved = await new PiChat(models, { ...config, codex_model: model }).resolveModel();
    const result = await models.completeSimple(resolved, { messages: [{ role: 'user', content: 'Reply with: ok', timestamp: Date.now() }] },
      { signal: AbortSignal.timeout(30_000), transport: 'sse', reasoning: 'low' });
    if (result.stopReason === 'error' || result.stopReason === 'aborted') {
      const { status, message } = remoteError(result.errorMessage ?? result.stopReason);
      return { ok: false, model: resolved.id, error: status ? `${status}: ${message}` : message };
    }
    return { ok: true, model: resolved.id };
  } catch (e) { return { ok: false, error: remoteError((e as Error).message).message }; }
}
