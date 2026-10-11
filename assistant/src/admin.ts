import { loadSkillsFromDir } from '@earendil-works/pi-coding-agent';
import { lstat, mkdir, mkdtemp, open, readdir, readFile, realpath, rename, rm, stat, writeFile } from 'node:fs/promises';
import lockfile from 'proper-lockfile';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { loadConfig, validateMailAccounts, validateUrgentSenders, type Config, type MailAccount } from './config.ts';
import type { GmailService } from '../.pi/extensions/gmail/service.ts';
import { MailTriage, triageMemory } from '../.pi/extensions/gmail/triage.ts';
import { MailError, mailAppAccounts, type MailTransport } from '../.pi/extensions/macos-mail/client.ts';
import type { Orchestrator } from './orchestrator.ts';
import { FileCredentials } from './credentials.ts';
import { liveRequests, recentActivity } from './activity.ts';
import { runProcess, type OwnedProcess } from './processes.ts';
import { PiChat } from './providers.ts';

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

export async function collectStatus(config: Config, app: Orchestrator, state: { busy: boolean; unloading: boolean }, activityFile: string | null) {
  const owned = [...(app.runtime.processes?.() ?? []), ...(app.speech.processes?.() ?? [])];
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
const FACTORY = new Set(['permissions.ts', 'request-workflow.ts', 'macos-mail', 'apple-reminders', 'gmail']);

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
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') { if (mustExist || index < segments.length - 1) throw Error('File not found'); }
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
    if ((await handle.stat()).size > MAX_FILE) throw Error('File too large to edit here');
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
    if (problems.length) throw Error('Skill validation failed: ' + problems.join('; '));
  } finally { await rm(scratch, { recursive: true, force: true }); }
}

async function atomicWrite(file: string, content: string, exclusive = false): Promise<void> {
  if (exclusive) {
    try { await writeFile(file, content, { flag: 'wx', mode: 0o644 }); return; }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'EEXIST') throw Error('Already exists'); throw error; }
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
  return { ok: true, note: 'Saved.' };
}

export async function createSkill(root: string, name: unknown, description: unknown) {
  if (typeof name !== 'string' || name.length > 64 || !NAME.test(name)) throw Error('Name: lowercase letters, digits and hyphens');
  if (typeof description !== 'string' || !description.trim() || description.length > 300 || /[\r\n]/.test(description)) throw Error('Description: one line, up to 300 characters');
  const content = `---\nname: ${name}\ndescription: ${JSON.stringify(description.trim())}\n---\n# ${name}\n\nDescribe the workflow here. Keep it short.\n`;
  await validateSkill(name, content);
  const directory = path.join(await realpath(base(root, 'skill')), name);
  try { await mkdir(directory); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'EEXIST') throw Error('Already exists'); throw error; }
  await atomicWrite(path.join(directory, 'SKILL.md'), content, true);
  return { ok: true, path: `${name}/SKILL.md`, note: 'Created.' };
}

/** Move a whole skill or extension out of the resource root into a trash directory (first that works); never a hard delete. */
export async function deleteResource(root: string, kind: unknown, name: unknown, trashDirs: string[]) {
  const directory = base(root, kind);
  if (typeof name !== 'string' || !SEGMENT.test(name) || name.length > 100) throw Error('Invalid name');
  if (kind === 'extension' && FACTORY.has(name)) throw Error('Built-in extensions cannot be deleted');
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
      return { ok: true, trashed: destination, note: `Moved to ${destination}.` };
    } catch (error) { lastError = error; }
  }
  throw lastError;
}

export async function createExtension(root: string, name: unknown) {
  if (typeof name !== 'string' || name.length > 64 || !NAME.test(name)) throw Error('Name: lowercase letters, digits and hyphens');
  const content = `import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';\n\n/** ${name}: describe what this extension does. */\nexport default function ${name.replace(/-([a-z0-9])/g, (_, c: string) => c.toUpperCase()).replace(/^[0-9]/, '_$&')}(_pi: ExtensionAPI) {\n  // Register tools with pi.registerTool(); new tools stay unavailable until approved in the harness.\n}\n`;
  await atomicWrite(path.join(await realpath(base(root, 'extension')), `${name}.ts`), content, true);
  return { ok: true, path: `${name}.ts`, note: 'Created.' };
}

/** Configured accounts plus the names Mail.app knows (null + error code when Mail cannot be asked). */
export async function listMailAccounts(config: Config, transport: MailTransport) {
  let available: string[] | null = null, available_error: string | undefined;
  try { available = await mailAppAccounts(transport, AbortSignal.timeout(15000)); }
  catch (error) { available_error = error instanceof MailError ? error.code : 'mail_unavailable'; }
  return { accounts: config.mail_accounts, available, ...(available_error ? { available_error } : {}) };
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
    const temporary = `${configPath}.${process.pid}.admin.tmp`;
    try {
      await writeFile(temporary, JSON.stringify(settings, null, 2) + '\n', { mode });
      await loadConfig(temporary); // The whole file must still load before it replaces config.json.
      await rename(temporary, configPath);
    } catch (error) { await rm(temporary, { force: true }); throw error; }
  } finally { await release(); }
}

/** Replaces mail_accounts in config.json (locked, validated, atomic); other settings are kept as they are. */
export async function saveMailAccounts(config: Config, configPath: string, value: unknown) {
  const accounts = validateMailAccounts(value);
  await updateConfigFile(configPath, settings => {
    for (const key of ['mail_enabled', 'mail_account', 'mail_mailbox']) delete settings[key];
    settings.mail_accounts = accounts;
  });
  config.mail_accounts = accounts; // Shared config object: the next Mail tool call sees the change.
  return { ok: true, accounts: accounts as readonly MailAccount[] };
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
  if (!Object.keys(change).length) throw Error('Nothing to save');
  await updateConfigFile(configPath, settings => { Object.assign(settings, change); }); // The whole file is validated before replacing.
  Object.assign(config, change);
  return { enabled: config.reminders_enabled, tasks_enabled: config.tasks_enabled, tasks_list: config.tasks_list };
}

/** Gmail: connection metadata and triage settings for Admin; never secrets or tokens. */
export async function gmailStatus(config: Config, service: GmailService) {
  const auth = await service.auth.status();
  return { ...auth, account: config.gmail_account, triage_enabled: config.gmail_triage_enabled,
    urgent_senders: config.gmail_urgent_senders, daily_limit: config.gmail_daily_limit,
    missing_labels: auth.connected ? await service.missingLabels() : [], error: service.auth.lastError };
}

/**
 * Gmail settings: OAuth client (Keychain only, never config.json) and account, triage switch and urgent senders
 * (config.json). Only the fields present are changed.
 */
export async function saveGmail(config: Config, configPath: string, service: GmailService, value: unknown) {
  const input = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  if (input.client_id !== undefined || input.client_secret !== undefined) await service.auth.saveClient(input.client_id, input.client_secret);
  const change: Partial<Pick<Config, 'gmail_account' | 'gmail_triage_enabled' | 'gmail_urgent_senders'>> = {};
  if (input.account !== undefined) {
    if (typeof input.account !== 'string' || (input.account.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.account.trim()))) throw Error('Invalid Gmail address');
    change.gmail_account = input.account.trim().toLowerCase();
  }
  if (input.triage_enabled !== undefined) { if (typeof input.triage_enabled !== 'boolean') throw Error('triage_enabled must be a boolean'); change.gmail_triage_enabled = input.triage_enabled; }
  if (input.urgent_senders !== undefined) change.gmail_urgent_senders = validateUrgentSenders(input.urgent_senders);
  if (Object.keys(change).length) {
    await updateConfigFile(configPath, settings => { Object.assign(settings, change); });
    Object.assign(config, change);
  }
  return gmailStatus(config, service);
}

/** Starts the browser consent; the Mac app opens `auth_url`. A first connection records the account in config.json. */
export async function connectGmail(config: Config, configPath: string, service: GmailService) {
  const { auth_url, done } = await service.auth.connect(config.gmail_account);
  done.then(async email => {
    service.forgetLabels();
    if (!config.gmail_account) await saveGmail(config, configPath, service, { account: email }).catch(() => {});
  }, () => {});
  return { auth_url };
}

export async function disconnectGmail(config: Config, service: GmailService) {
  await service.auth.disconnect(); service.forgetLabels();
  return gmailStatus(config, service);
}

const adminTriage = (config: Config, service: GmailService) => new MailTriage({ provider: service.provider, settings: config, memory: triageMemory,
  turn: 0, logFile: service.logFile, stateFile: service.stateFile, importance: () => service.importance() });
export async function gmailActions(config: Config, service: GmailService) {
  return { batches: await adminTriage(config, service).recent(10) };
}
export async function undoGmail(config: Config, service: GmailService) {
  const result = await adminTriage(config, service).undo(AbortSignal.timeout(60_000));
  return { ...result, batches: await adminTriage(config, service).recent(10) };
}
