import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import type { Config } from './config.ts';
import type { Orchestrator } from './orchestrator.ts';
import { NoSpeechError } from './speech.ts';
import { activity, observeRequest } from './activity.ts';
import { root } from './config.ts';
import { collectStatus, connectGmail, createExtension, createSkill, deleteMemory, deleteResource, memoryStatus, saveMemoryItem, saveMemoryTopic, saveMemorySettings, disconnectGmail, filesStatus, gmailActions, gmailStatus, listResources,
  readResource, remoteModels, saveRemoteModel, testRemoteModel, saveFiles, saveGmail, saveReminders, saveVault, undoGmail, unlockVault, vaultStatus, writeResource } from './admin.ts';
import { registerSecretSpeaker, registeredSecretSpeaker, VaultError, vaultService, type SecretSpeaker, type VaultService } from './vault.ts';
import { fileService, type FileService } from './files.ts';
import { readPronunciations, savePronunciations } from './pronounce.ts';
import { gmailService, type GmailService } from '../.pi/extensions/gmail/service.ts';
import { GmailError } from '../.pi/extensions/gmail/schemas.ts';
import path from 'node:path';
import { homedir } from 'node:os';
import { setTimeout as sleep } from 'node:timers/promises';
import { TaskScheduler, TaskService, migrateReminders, osascriptNotify, registerTasks, registeredTasks, type Notify } from './tasks.ts';
import { TaskStore } from './task-store.ts';
import { AnnouncementQueue, updatesSource, type AnnouncementSource } from './announce.ts';
import { registerUpdates, UpdateManager, type Summarize } from './updates.ts';
import { exec, type Restart } from './components.ts';
import type { Speech } from './speech.ts';
import { PiChat, Steering, type Chat } from './providers.ts';
import { osascriptTransport, type RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';

/** Assistant tasks wiring (`data/tasks.sqlite`); omitted = no scheduler (tests). `transport`: Reminders, only for the one-time migration. */
export type TaskOptions = { store?: TaskStore; dbFile?: string; transport?: RemindersTransport; notify?: Notify; firstMs?: number; everyMs?: number; now?: () => number };
/** Component updates wiring; omitted = no updater (tests). */
export type UpdateOptions = { manager?: UpdateManager; stateFile: string | null; notify?: Notify; firstMs?: number; everyMs?: number };

function beginRequestSlot() {
  let finish!: () => void;
  const done = new Promise<void>(resolve => { finish = resolve; });
  return { controller: new AbortController(), done, finish };
}

async function readBody(request: IncomingMessage, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = []; let size = 0;
  for await (const value of request) {
    const chunk = Buffer.from(value); size += chunk.length;
    if (size > limit) throw Error('Request exceeds size limit');
    chunks.push(chunk);
  }
  if (!size) throw Error('Empty request');
  return Buffer.concat(chunks);
}

/**
 * Stops accepting, drops idle keep-alive sockets now and every socket after `graceMs` (long-polls answer on abort
 * first). Without this a keep-alive client that keeps polling holds close() open.
 */
export function closeServer(server: Server, graceMs = 1000): Promise<void> {
  return new Promise(resolve => {
    if (!server.listening) return resolve();
    server.close(() => resolve());
    server.closeIdleConnections();
    const idle = setInterval(() => server.closeIdleConnections(), 100); idle.unref();
    const all = setTimeout(() => server.closeAllConnections(), graceMs); all.unref();
    server.once('close', () => { clearInterval(idle); clearTimeout(all); });
  });
}

export async function makeServer(config: Config, app: Orchestrator, lifecycle: AbortSignal, activityFile: string | null = path.join(root, 'data/activity.log'), resourceRoot = root,
  trashDirs = [path.join(homedir(), '.Trash'), path.join(resourceRoot, 'data/trash')],
  mail: { configPath?: string; gmail?: GmailService; files?: FileService; vault?: VaultService } = {}, tasks?: TaskOptions, updateOptions?: UpdateOptions) {
  const vault = mail.vault ?? vaultService(config);
  const gmail = mail.gmail ?? gmailService(root);
  const files = mail.files ?? fileService(config);
  const configPath = mail.configPath ?? path.join(root, 'config.json');
  const observe = <T>(details: Record<string, string | number | boolean>, action: () => Promise<T>) => activityFile ? observeRequest(activityFile, details, action) : action();
  const token = randomBytes(32).toString('base64url');
  let active: { controller: AbortController; done: Promise<void>; finish: () => void; text: string; steering: Steering; replaced: boolean; readAloud: boolean } | undefined;
  let unloading = false;
  // An automated Reminders task holds this slot; it starts only when no user request runs; a user request cancels it.
  let task: ReturnType<typeof beginRequestSlot> | undefined;
  // Local/remote choice for tasks follows the ring's mode (last chat or /mode).
  let offlineMode = false;
  const transcriptions = new Set<AbortController>();
  const beginRequest = beginRequestSlot;
  // Background file indexing waits while the user's request or a task runs.
  files.busy = () => Boolean(active || task || unloading);
  // vault_copy speak: the spelled value goes straight to TTS, only while the ring reads replies aloud.
  const speakSecret: SecretSpeaker = async (spelled, signal) => {
    if (active && !active.readAloud) throw new VaultError('speech_off');
    if (!app.speech.speakSecret) throw new VaultError('speech_unavailable');
    return app.speech.speakSecret(spelled, AbortSignal.any([signal, lifecycle]));
  };
  registerSecretSpeaker(speakSecret);
  lifecycle.addEventListener('abort', () => { if (registeredSecretSpeaker() === speakSecret) registerSecretSpeaker(undefined); }, { once: true });
  const lane = {
    async acquire(signal: AbortSignal) {
      while (active || unloading || task) await sleep(250, undefined, { signal: AbortSignal.any([signal, lifecycle]) });
      const slot = beginRequestSlot(); task = slot;
      return { signal: AbortSignal.any([lifecycle, slot.controller.signal, signal]),
        release: () => { if (task === slot) task = undefined; slot.finish(); } };
    }
  };
  const taskStore = tasks ? tasks.store ?? new TaskStore(tasks.dbFile ?? path.join(root, 'data/tasks.sqlite')) : undefined;
  let taskService: TaskService | undefined;
  if (tasks && taskStore) {
    const transport = tasks.transport ?? osascriptTransport();
    const now = tasks.now ?? (() => Date.now());
    const scheduler = new TaskScheduler({ config, store: taskStore, lane, notify: tasks.notify, activityFile, now,
      run: (prompt, intentText, signal) => app.ask(prompt, offlineMode, signal, { automated: true, intentText }),
      migrate: signal => migrateReminders({ store: taskStore, transport, config, signal, now: now() }) });
    taskService = new TaskService(taskStore, scheduler, now);
    registerTasks(taskService);
    lifecycle.addEventListener('abort', () => { if (registeredTasks() === taskService) registerTasks(undefined); }, { once: true });
    scheduler.start(lifecycle, tasks.firstMs, tasks.everyMs);
  }
  // Release-note summaries: the local model in the request lane (never during a request); remote only if local is unavailable.
  const plain = (chat: Chat, local: boolean): Chat => chat instanceof PiChat ? new PiChat(chat.models, config, local) : chat;
  const summarize: Summarize = async (system, user, signal) => {
    const slot = await lane.acquire(signal);
    const context = { systemPrompt: system, messages: [{ role: 'user' as const, content: user, timestamp: Date.now() }] };
    const text = (message: { content: { type: string; text?: string }[] }) => message.content.filter(b => b.type === 'text').map(b => b.text ?? '').join('\n');
    try {
      const started = !(await app.runtime.healthy?.(slot.signal));
      try {
        await app.runtime.ensure(slot.signal);
        return text(await plain(app.local, true).complete(context, AbortSignal.any([slot.signal, AbortSignal.timeout(config.local_timeout_ms)])));
      } catch (error) {
        slot.signal.throwIfAborted();
        if (offlineMode) throw error;
        return text(await plain(app.remote, false).complete(context, AbortSignal.any([slot.signal, AbortSignal.timeout(config.remote_timeout_ms)])));
      } finally { if (started && !offlineMode) await app.runtime.close(); }
    } finally { slot.release(); }
  };
  // Memory extraction (src/memory.ts): in the request lane, after a request, cancelled by the next one. Local mode: the
  // local model (loaded if needed, idle only); remote mode: the remote model, so the local model is never loaded for it.
  const memory = app.memory;
  if (memory) {
    memory.extractor.lane = lane;
    memory.extractor.offline = () => offlineMode;
    memory.extractor.model ??= async (system, user, local, signal) => {
      const context = { systemPrompt: system, messages: [{ role: 'user' as const, content: user, timestamp: Date.now() }] };
      const text = (message: { content: { type: string; text?: string }[] }) => message.content.filter(b => b.type === 'text').map(b => b.text ?? '').join('\n');
      if (!local) return text(await plain(app.remote, false).complete(context, AbortSignal.any([signal, AbortSignal.timeout(config.remote_timeout_ms)])));
      const started = !(await app.runtime.healthy?.(signal));
      try {
        await app.runtime.ensure(signal);
        return text(await plain(app.local, true).complete(context, AbortSignal.any([signal, AbortSignal.timeout(config.local_timeout_ms)])));
      } finally { if (started && !offlineMode) await app.runtime.close(); }
    };
    lifecycle.addEventListener('abort', () => memory.extractor.close(), { once: true });
  }
  /** Restarts only what an update touched; runs inside the lane, so no request is using it. Lazy processes start again on use. */
  const restart = async (target: Restart): Promise<string | void> => {
    const speech = app.speech as Partial<Speech>;
    if (target === 'llama') { await app.runtime.close(); await files.embedder.close(); }
    else if (target === 'whisper') await speech.whisper?.close();
    else if (target === 'ema') await speech.ema?.close();
    else if (target === 'kokoro') await speech.kokoro?.close();
    else if (target === 'app') {
      // Rebuild only; the restart itself is asked of the native app at an idle moment (announce/wait `restart`).
      if (process.platform !== 'darwin') return;
      const result = await exec(path.join(path.dirname(process.execPath), 'npm'), ['run', 'rebuild-app'], { cwd: root, signal: lifecycle, timeoutMs: 10 * 60_000 });
      if (result.code !== 0) throw Error('npm run rebuild-app failed');
    }
  };
  /** Post-update health of lazily started workers: whisper server health, EMA/Kokoro worker ready handshake; stopped again after. */
  const probe = async (target: Restart, signal: AbortSignal): Promise<void> => {
    const speech = app.speech as Partial<Speech>;
    if (target === 'whisper' && speech.whisper) { await speech.whisper.ensure(signal); await speech.whisper.close(); }
    else if (target === 'ema' && speech.ema) { await speech.ema.ping(signal); await speech.ema.close(); }
    else if (target === 'kokoro' && speech.kokoro) { await speech.kokoro.ping(signal); await speech.kokoro.close(); }
  };
  const updates = updateOptions ? updateOptions.manager ?? new UpdateManager({ root, stateFile: updateOptions.stateFile, lane, restart, summarize, probe,
    busy: () => Boolean(active || task || unloading),
    notify: updateOptions.notify ?? osascriptNotify, activityFile, checkHour: () => config.update_check_hour, llamaModel: config.llama_model }) : undefined;
  const announceSources: AnnouncementSource[] = [];
  if (taskService?.scheduler) announceSources.push(taskService.scheduler);
  if (updates) announceSources.push(updatesSource(updates));
  const announcements = new AnnouncementQueue(announceSources);
  if (updates && updateOptions) {
    registerUpdates(updates);
    lifecycle.addEventListener('abort', () => registerUpdates(undefined), { once: true });
    updates.start(lifecycle, updateOptions.firstMs, updateOptions.everyMs);
  }
  const server = createServer(async (request, response) => {
    const reply = (code: number, data: unknown) => {
      if (response.destroyed) return;
      const body = JSON.stringify(data);
      response.writeHead(code, { 'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
        // Shutting down: no keep-alive, so the app's polling cannot hold server.close() open.
        ...(lifecycle.aborted ? { Connection: 'close' } : {}) });
      response.end(body);
    };
    const address = server.address();
    const origin = `http://127.0.0.1:${address && typeof address === 'object' ? address.port : config.port}`;
    if (request.headers.host !== origin.slice(7)) return reply(403, { error: 'Invalid host' });
    if (request.method === 'GET' && request.url !== '/session') return reply(404, { error: 'Not found' });
    if (request.method === 'GET' && request.url === '/session') return reply(200, { token, max_record_seconds: config.max_record_seconds });
    const supplied = Buffer.from(String(request.headers['x-assistant-token'] || ''));
    const expected = Buffer.from(token);
    if (request.method !== 'POST' || (request.headers.origin && request.headers.origin !== origin) ||
        supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return reply(403, { error: 'Invalid session' });
    try {
      const body = await readBody(request, request.url === '/transcribe' ? config.max_record_seconds * 32000 + 4096 : request.url === '/admin/write' ? 600000 : 64000);
      if (request.url === '/transcribe') {
        // Speech-to-text runs beside an active chat (separate whisper server); only an explicit cancel stops it.
        const controller = new AbortController(); transcriptions.add(controller);
        try { reply(200, { text: await observe({ kind: 'transcription', audio_bytes: body.length }, () => app.speech.transcribe(body, AbortSignal.any([lifecycle, controller.signal]))) }); }
        finally { transcriptions.delete(controller); }
        return;
      }
      const data: unknown = JSON.parse(body.toString());
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw Error('Expected an object');
      const input = data as { text?: unknown; offline?: unknown };
      // Native admin window (user actions; read-only status plus app-owned skill/extension files).
      if (request.url === '/status') return reply(200, await collectStatus(config, app, { busy: Boolean(active || task), unloading }, activityFile, files));
      if (request.url?.startsWith('/admin/')) {
        const value = data as { kind?: unknown; path?: unknown; content?: unknown; name?: unknown; description?: unknown };
        if (request.url === '/admin/resources') return reply(200, await listResources(resourceRoot));
        if (request.url === '/admin/read') return reply(200, await readResource(resourceRoot, value.kind, value.path));
        if (request.url === '/admin/write') return reply(200, await writeResource(resourceRoot, value.kind, value.path, value.content));
        if (request.url === '/admin/create-skill') return reply(200, await createSkill(resourceRoot, value.name, value.description));
        if (request.url === '/admin/delete') return reply(200, await deleteResource(resourceRoot, value.kind, value.name, trashDirs));
        if (request.url === '/admin/create-extension') return reply(200, await createExtension(resourceRoot, value.name));
        if (request.url === '/admin/reminders') return reply(200, { enabled: config.reminders_enabled, tasks_enabled: config.tasks_enabled, tasks_list: config.tasks_list });
        if (request.url === '/admin/reminders/save') return reply(200, await saveReminders(config, configPath, data));
        if (request.url?.startsWith('/admin/remote-model')) {
          const models = app.remote instanceof PiChat ? app.remote.models : undefined;
          if (request.url === '/admin/remote-model') return reply(200, await remoteModels(config, models));
          if (request.url === '/admin/remote-model/save') return reply(200, await saveRemoteModel(config, configPath, models, data));
          if (request.url === '/admin/remote-model/test') return reply(200, await testRemoteModel(config, models, data));
        }
        if (request.url === '/admin/gmail') return reply(200, await gmailStatus(config, gmail));
        if (request.url === '/admin/gmail/save') return reply(200, await saveGmail(config, configPath, gmail, data));
        if (request.url === '/admin/gmail/connect') return reply(200, await connectGmail(gmail));
        if (request.url === '/admin/gmail/disconnect') return reply(200, await disconnectGmail(config, gmail, data));
        if (request.url === '/admin/gmail/importance') return reply(200, { content: await gmail.importance() });
        if (request.url === '/admin/gmail/importance/save') return reply(200, await gmail.saveImportance((data as { content?: unknown }).content));
        if (request.url === '/admin/pronunciations') return reply(200, { content: await readPronunciations() });
        if (request.url === '/admin/pronunciations/save') return reply(200, await savePronunciations((data as { content?: unknown }).content));
        if (request.url === '/admin/gmail/actions') return reply(200, await gmailActions(config, gmail));
        if (request.url === '/admin/gmail/undo') return reply(200, await undoGmail(config, gmail));
        // Bitwarden: the unlock body holds the master password; it is passed to the vault only and never logged.
        if (request.url === '/admin/bitwarden') return reply(200, await vaultStatus(config, vault));
        if (request.url === '/admin/bitwarden/save') return reply(200, await saveVault(config, configPath, vault, data));
        if (request.url === '/admin/bitwarden/unlock') return reply(200, await unlockVault(config, vault, data));
        if (request.url === '/admin/bitwarden/lock') { await vault.lock(); return reply(200, await vaultStatus(config, vault)); }
        if (request.url === '/admin/files') return reply(200, await filesStatus(config, files));
        if (request.url === '/admin/files/save') return reply(200, await saveFiles(config, configPath, files, data));
        if (request.url?.startsWith('/admin/updates')) {
          if (!updates) return reply(404, { error: 'Updates are off' });
          const value = data as { id?: unknown; ids?: unknown; version?: unknown };
          const ids = Array.isArray(value.ids) ? value.ids.filter((id): id is string => typeof id === 'string') : typeof value.id === 'string' ? [value.id] : [];
          if (request.url === '/admin/updates') return reply(200, await updates.list());
          if (request.url === '/admin/updates/check') { void updates.check(lifecycle).catch(() => []); await sleep(100); return reply(200, await updates.list()); }
          if (request.url === '/admin/updates/apply') { updates.startApply(ids); return reply(200, await updates.list()); }
          if (request.url === '/admin/updates/rollback') { updates.startRollback(ids); return reply(200, await updates.list()); }
          if (request.url === '/admin/updates/skip') return reply(200, await updates.skip(value.id, value.version));
          return reply(404, { error: 'Not found' });
        }
        if (request.url?.startsWith('/admin/tasks')) {
          if (!taskService) return reply(404, { error: 'Tasks are off' });
          if (request.url === '/admin/tasks') return reply(200, { enabled: config.tasks_enabled, tasks: taskService.list() });
          if (request.url === '/admin/tasks/action') {
            const value = data as { id?: unknown; action?: unknown };
            const found = Number.isInteger(value.id) ? taskStore!.get(value.id as number) : undefined;
            if (!found) throw Error('Görev bulunamadı');
            if (value.action === 'pause') taskService.pause(found);
            else if (value.action === 'resume') taskService.resume(found);
            else if (value.action === 'run') taskService.runNow(found);
            else if (value.action === 'delete') taskService.remove(found);
            else throw Error('Invalid action');
            return reply(200, { enabled: config.tasks_enabled, tasks: taskService.list() });
          }
          return reply(404, { error: 'Not found' });
        }
        if (request.url?.startsWith('/admin/memory')) {
          if (!memory) return reply(404, { error: 'Memory is off' });
          if (request.url === '/admin/memory') return reply(200, await memoryStatus(config, memory));
          if (request.url === '/admin/memory/settings') return reply(200, await saveMemorySettings(config, configPath, memory, data));
          if (request.url === '/admin/memory/save') return reply(200, await saveMemoryItem(config, memory, data));
          if (request.url === '/admin/memory/topic') return reply(200, await saveMemoryTopic(config, memory, data));
          if (request.url === '/admin/memory/delete') return reply(200, await deleteMemory(config, memory, data));
          return reply(404, { error: 'Not found' });
        }
        if (request.url === '/admin/files/reindex') return reply(200, { ...files.reindex(lifecycle), ...await filesStatus(config, files) });
        return reply(404, { error: 'Not found' });
      }
      // Native secure input for vault_save (source "prompt"): the app long-polls, shows NSSecureTextField and answers.
      // The answer body holds the secret; it goes to the vault only and is never logged or returned.
      if (request.url === '/secret-prompt/wait') {
        const closed = new AbortController(); response.once('close', () => closed.abort());
        return reply(200, await vault.prompts.wait(AbortSignal.any([lifecycle, closed.signal, AbortSignal.timeout(25_000)])) ?? {});
      }
      if (request.url === '/secret-prompt/answer') {
        const value = data as { id?: unknown; value?: unknown; cancel?: unknown };
        return reply(200, { ok: vault.prompts.answer(value.id, value.cancel === true ? undefined : value.value) });
      }
      // Proactive messages (task results/errors, update offer and results): the ring long-polls; only offered while nothing runs.
      if (request.url === '/announce/wait') {
        const closed = new AbortController(); response.once('close', () => closed.abort());
        const wait = AbortSignal.any([lifecycle, closed.signal, AbortSignal.timeout(25_000)]);
        while (announceSources.length && !wait.aborted) {
          const next = !active && !task && !unloading ? await announcements.next() : undefined;
          if (next) return reply(200, next);
          await sleep(1000, undefined, { signal: wait }).catch(() => {});
        }
        return reply(200, {});
      }
      if (request.url === '/announce/shown') return reply(200, { ok: await announcements.shown((data as { id?: unknown }).id) });
      if (request.url === '/cancel') { active?.controller.abort(); for (const t of transcriptions) t.abort(); return reply(200, { ok: true }); }
      if (request.url === '/mode') {
        if (typeof input.offline !== 'boolean') throw Error('Invalid mode');
        offlineMode = input.offline; return reply(200, { ok: true });
      }
      if (request.url === '/unload-local') {
        if (unloading) return reply(409, { error: 'Model is already unloading' });
        unloading = true;
        try {
          const current = active, running = task;
          current?.controller.abort(); running?.controller.abort(); // A cancelled task is retried on a later tick.
          if (current) await current.done;
          if (running) await running.done;
          await app.unloadLocal();
          return reply(200, { ok: true });
        } finally { unloading = false; }
      }
      if (request.url === '/dictation') {
        // The host pasted a voice transcript into another app's field instead of asking the assistant: app id + length only.
        const value = data as { app?: unknown; text_chars?: unknown };
        if (typeof value.app !== 'string' || !/^[\w .-]{1,200}$/.test(value.app) || !Number.isInteger(value.text_chars) ||
            (value.text_chars as number) < 1 || (value.text_chars as number) > 1_000_000) throw Error('Invalid dictation');
        const details = { app: value.app, text_chars: value.text_chars as number };
        await observe({ kind: 'dictation' }, async () => activity('dictation', details)); return reply(200, { ok: true });
      }
      if (request.url === '/stop-speech') { app.speech.stop(); return reply(200, { ok: true }); }
      // Recording: hold reply audio so it does not reach the microphone, then continue it.
      if (request.url === '/pause-speech') { app.speech.pause?.(); return reply(200, { ok: true }); }
      if (request.url === '/resume-speech') { app.speech.resume?.(); return reply(200, { ok: true }); }
      if (request.url === '/speak') {
        if (typeof input.text !== 'string' || !input.text.length || input.text.length > 12000) throw Error('Invalid speech text');
        await observe({ kind: 'speech', text_chars: input.text.length }, () => app.speech.speak(input.text as string, lifecycle)); return reply(200, { ok: true });
      }
      if (!['/chat', '/reset'].includes(request.url || '')) return reply(404, { error: 'Not found' });
      if ((request.url === '/reset' && active) || unloading) return reply(409, { error: 'Assistant is busy' });
      if (request.url === '/reset') { app.reset(); return reply(200, { ok: true }); }
      const readAloud = (data as { read_aloud?: unknown }).read_aloud;
      if (typeof input.text !== 'string' || (input.offline !== undefined && typeof input.offline !== 'boolean') ||
          (readAloud !== undefined && typeof readAloud !== 'boolean')) throw Error('Invalid chat request');
      let text = input.text;
      if (active) {
        // Barge-in: a request that is still generating takes the new text as a steer (one reply, on its response).
        if (active.steering.steer(text)) return reply(200, { steered: true });
        // Not steerable (no live Pi session yet, or finishing): cancel it and start now with both texts.
        const previous = active; previous.replaced = true; previous.controller.abort(); await previous.done;
        text = previous.text + '\n\n' + text;
        if (active || unloading) return reply(409, { error: 'Assistant is busy' });
      }
      // The user comes first: a running automated task is cancelled; its slot is re-armed and runs again after this request.
      task?.controller.abort();
      const current = { ...beginRequest(), text, steering: new Steering(), replaced: false, readAloud: readAloud !== false }; active = current; offlineMode = input.offline === true;
      const disconnected = () => { if (!response.writableEnded) current.controller.abort(); };
      response.once('close', disconnected);
      try {
        const cancelled = new Promise(resolve => current.controller.signal.addEventListener('abort', resolve, { once: true }));
        while (task) { await Promise.race([task.done, cancelled]); current.controller.signal.throwIfAborted(); }
        reply(200, await observe({ kind: 'chat', offline: input.offline === true, text_chars: text.length }, () => app.ask(text, input.offline === true, AbortSignal.any([lifecycle, current.controller.signal]), undefined, current.steering))); }
      catch (error) { if (current.replaced) return reply(409, { error: 'Replaced by a newer request', code: 'REPLACED' }); throw error; }
      finally { response.off('close', disconnected); if (active === current) active = undefined; current.finish(); }
    } catch (error) { reply(400, { error: lifecycle.aborted ? 'Assistant is shutting down' : error instanceof GmailError ? error.code : error instanceof Error ? error.message : 'Request failed',
      ...(error instanceof NoSpeechError ? { code: 'NO_SPEECH' } : {}) }); }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  return server;
}
