import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import type { Config } from './config.ts';
import type { Orchestrator } from './orchestrator.ts';
import { NoSpeechError } from './speech.ts';
import { activity, observeRequest } from './activity.ts';
import { root } from './config.ts';
import { collectStatus, createExtension, createSkill, deleteResource, listMailAccounts, listResources, readResource, saveMailAccounts, writeResource } from './admin.ts';
import type { MailTransport } from '../.pi/extensions/macos-mail/client.ts';
import { defaultMailTransport } from '../.pi/extensions/macos-mail/mail-db.ts';
import path from 'node:path';
import { homedir } from 'node:os';

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

export async function makeServer(config: Config, app: Orchestrator, lifecycle: AbortSignal, activityFile: string | null = path.join(root, 'data/activity.log'), resourceRoot = root,
  trashDirs = [path.join(homedir(), '.Trash'), path.join(resourceRoot, 'data/trash')],
  mail: { configPath?: string; transport?: MailTransport } = {}) {
  const configPath = mail.configPath ?? path.join(root, 'config.json');
  const observe = <T>(details: Record<string, string | number | boolean>, action: () => Promise<T>) => activityFile ? observeRequest(activityFile, details, action) : action();
  const token = randomBytes(32).toString('base64url');
  let active: { controller: AbortController; done: Promise<void>; finish: () => void } | undefined;
  let unloading = false;
  const transcriptions = new Set<AbortController>();
  const beginRequest = () => {
    let finish!: () => void;
    const done = new Promise<void>(resolve => { finish = resolve; });
    return { controller: new AbortController(), done, finish };
  };
  const server = createServer(async (request, response) => {
    const reply = (code: number, data: unknown) => {
      if (response.destroyed) return;
      const body = JSON.stringify(data);
      response.writeHead(code, { 'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'" });
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
      if (request.url === '/status') return reply(200, await collectStatus(config, app, { busy: Boolean(active), unloading }, activityFile));
      if (request.url?.startsWith('/admin/')) {
        const value = data as { kind?: unknown; path?: unknown; content?: unknown; name?: unknown; description?: unknown };
        if (request.url === '/admin/resources') return reply(200, await listResources(resourceRoot));
        if (request.url === '/admin/read') return reply(200, await readResource(resourceRoot, value.kind, value.path));
        if (request.url === '/admin/write') return reply(200, await writeResource(resourceRoot, value.kind, value.path, value.content));
        if (request.url === '/admin/create-skill') return reply(200, await createSkill(resourceRoot, value.name, value.description));
        if (request.url === '/admin/delete') return reply(200, await deleteResource(resourceRoot, value.kind, value.name, trashDirs));
        if (request.url === '/admin/create-extension') return reply(200, await createExtension(resourceRoot, value.name));
        if (request.url === '/admin/mail/accounts') return reply(200, await listMailAccounts(config, mail.transport ?? defaultMailTransport()));
        if (request.url === '/admin/mail/save') return reply(200, await saveMailAccounts(config, configPath, (data as { accounts?: unknown }).accounts));
        return reply(404, { error: 'Not found' });
      }
      if (request.url === '/cancel') { active?.controller.abort(); for (const t of transcriptions) t.abort(); return reply(200, { ok: true }); }
      if (request.url === '/unload-local') {
        if (unloading) return reply(409, { error: 'Model is already unloading' });
        unloading = true;
        try {
          const current = active;
          current?.controller.abort();
          if (current) await current.done;
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
      if (active || unloading) return reply(409, { error: 'Assistant is busy' });
      if (request.url === '/reset') { app.reset(); return reply(200, { ok: true }); }
      if (typeof input.text !== 'string' || (input.offline !== undefined && typeof input.offline !== 'boolean')) throw Error('Invalid chat request');
      const current = beginRequest(); active = current;
      const disconnected = () => { if (!response.writableEnded) current.controller.abort(); };
      response.once('close', disconnected);
      try { reply(200, await observe({ kind: 'chat', offline: input.offline === true, text_chars: input.text.length }, () => app.ask(input.text as string, input.offline === true, AbortSignal.any([lifecycle, current.controller.signal])))); }
      finally { response.off('close', disconnected); if (active === current) active = undefined; current.finish(); }
    } catch (error) { reply(400, { error: lifecycle.aborted ? 'Assistant is shutting down' : error instanceof Error ? error.message : 'Request failed',
      ...(error instanceof NoSpeechError ? { code: 'NO_SPEECH' } : {}) }); }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  return server;
}
