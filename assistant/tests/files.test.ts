import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { deflateRawSync } from 'node:zlib';
import { mkdtemp, mkdir, readdir, readFile, realpath, rm, stat, symlink, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defaults, validateFileRoots, validateSkipDirs, type Config } from '../src/config.ts';
import { EmbeddingRuntime, truncate } from '../src/embeddings.ts';
import { chunkText, htmlText, kindOf, officeText } from '../src/files-extract.ts';
import { FileIndex, nameScore, normalizeName, priority, type Found } from '../src/files-index.ts';
import { cluster, FileOrganizer, FilesMemory, folderName } from '../src/files-organize.ts';
import { FileService } from '../src/files.ts';
import { filesReady, harnessTools } from '../src/harness.ts';
import { permissionsExtension } from '../.pi/extensions/permissions.ts';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';

/** Fake embedding server: hashed bag of words (text) or a hash of the bytes (media), 768-d, L2-normalized. */
async function fakeServer() {
  let requests = 0, inputs = 0;
  const vector = (words: string[]) => {
    const v = new Array(768).fill(0);
    for (const w of words) v[createHash('md5').update(w).digest().readUInt16LE(0) % 256] += 1;
    const n = Math.hypot(...v) || 1;
    return v.map(x => x / n);
  };
  const server = createServer(async (request, response) => {
    if (request.url === '/health') { response.end(JSON.stringify({ status: 'ok' })); return; }
    const chunks: Buffer[] = []; for await (const c of request) chunks.push(c as Buffer);
    const body = JSON.parse(Buffer.concat(chunks).toString()) as { input: unknown[] };
    requests++; inputs += body.input.length;
    const data = body.input.map((item, index) => {
      if (typeof item === 'string') {
        const text = item.replace(/^task: [^|]+\| query: /, '').replace(/^title: [^|]*\| text: /, '');
        return { index, embedding: vector(text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length > 2)) };
      }
      const part = (item as { content: { type: string }[] }).content[0];
      return { index, embedding: vector([part.type === 'image_url' ? 'picture' : 'recording', 'media']) };
    });
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ data }));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address() as { port: number };
  return { url: `http://127.0.0.1:${address.port}`, stats: () => ({ requests, inputs }), close: () => new Promise<void>(r => server.close(() => r())) };
}

async function setup(options: { mmproj?: boolean } = {}) {
  const fake = await fakeServer();
  const dir = await realpath(await mkdtemp(path.join(tmpdir(), 'ayhan-files-')));
  const docs = path.join(dir, 'Docs');
  await mkdir(docs);
  const config: Config = { ...defaults, embedding_enabled: true, embedding_url: fake.url, file_roots: [docs],
    embedding_mmproj: options.mmproj ? 'mmproj.gguf' : '' };
  const embedder = new EmbeddingRuntime(config, false);
  const service = new FileService(config, { embedder, dataDir: path.join(dir, 'data'), activityFile: null });
  const close = async () => { await service.close(); await fake.close(); await rm(dir, { recursive: true, force: true }); };
  return { fake, dir, docs, config, service, index: service.index, close };
}

test('extraction: kinds, html, office zip and chunking', () => {
  assert.equal(kindOf('a.MD'), 'text'); assert.equal(kindOf('b.pdf'), 'pdf'); assert.equal(kindOf('c.docx'), 'office');
  assert.equal(kindOf('d.heic'), 'image'); assert.equal(kindOf('e.m4a'), 'audio'); assert.equal(kindOf('f.mov'), 'video'); assert.equal(kindOf('g.bin'), 'other');
  assert.equal(htmlText('<p>Hello&amp;bye</p><script>x()</script><div>Two</div>'), 'Hello&bye\nTwo');
  // A one-entry deflated docx.
  const xml = Buffer.from('<w:document><w:body><w:p><w:r><w:t>Kira sözleşmesi</w:t></w:r></w:p><w:p><w:t>Fesih: iki ay</w:t></w:p></w:body></w:document>');
  const data = deflateRawSync(xml), name = Buffer.from('word/document.xml');
  const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(8, 8); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(xml.length, 22); local.writeUInt16LE(name.length, 26);
  const central = Buffer.alloc(46); central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(8, 10); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(xml.length, 24); central.writeUInt16LE(name.length, 28); central.writeUInt32LE(0, 42);
  const cdOffset = local.length + name.length + data.length;
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10); end.writeUInt32LE(central.length + name.length, 12); end.writeUInt32LE(cdOffset, 16);
  const zip = Buffer.concat([local, name, data, central, name, end]);
  assert.equal(officeText(zip, 'docx'), 'Kira sözleşmesi\nFesih: iki ay');
  const chunks = chunkText('a'.repeat(1000) + '\n' + 'b'.repeat(1000) + '\n' + 'c'.repeat(1000), 1500, 20);
  assert.equal(chunks.length, 3); assert.ok(chunks.every(c => c.length <= 1500));
  assert.equal(chunkText('x'.repeat(10_000), 1000, 3).length, 3);
});

test('MRL truncation keeps the first dims and re-normalizes', () => {
  const v = truncate([3, 4, 100, 100], 2);
  assert.deepEqual([...v].map(x => Math.round(x * 10) / 10), [0.6, 0.8]);
});

test('config: file roots are validated', () => {
  assert.deepEqual(defaults.file_roots, ['~/Documents']);
  assert.deepEqual(validateFileRoots(['~/Documents/', '~/Documents', '/tmp/x']), ['~/Documents', '/tmp/x']);
  for (const bad of [['relative/path'], ['/'], ['~'], [''], Array(11).fill('/tmp/a'), 'x'])
    assert.throws(() => validateFileRoots(bad));
});

test('indexing is incremental, skips hidden/bundles/symlinks, drops removed files and ranks by meaning', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.docs, 'rent.txt'), 'Rental contract. The tenant must give two months notice before leaving the apartment.');
    await writeFile(path.join(f.docs, 'recipe.md'), '# Pancakes\nFlour, eggs, milk and sugar. Fry in a pan.');
    await mkdir(path.join(f.docs, 'Work'));
    await writeFile(path.join(f.docs, 'Work', 'report.html'), '<h1>Quarterly budget</h1><p>Budget meeting moved to Friday.</p>');
    await writeFile(path.join(f.docs, '.hidden.txt'), 'secret notice tenant');
    await mkdir(path.join(f.docs, 'node_modules')); await writeFile(path.join(f.docs, 'node_modules', 'x.txt'), 'tenant');
    await mkdir(path.join(f.docs, 'Tool.app')); await writeFile(path.join(f.docs, 'Tool.app', 'x.txt'), 'tenant');
    const outside = path.join(f.dir, 'outside.txt'); await writeFile(outside, 'tenant notice outside');
    await symlink(outside, path.join(f.docs, 'link.txt'));
    await f.service.runOnce(AbortSignal.timeout(10_000));
    let status = await f.index.status();
    assert.equal(status.files, 3);
    let first = f.fake.stats().requests;
    await f.service.runOnce(AbortSignal.timeout(10_000));
    assert.equal(f.fake.stats().requests, first, 'unchanged files are not embedded again');
    const hits = await f.index.search('tenant notice months', 3, AbortSignal.timeout(5000));
    assert.equal(hits[0].name, 'rent.txt');
    assert.ok(hits[0].snippet.length <= 200 && hits[0].snippet.includes('tenant'));
    assert.ok(!hits[0].path.startsWith('/') || hits[0].path.includes('Docs'));
    assert.equal((await f.index.search('budget friday', 1, AbortSignal.timeout(5000)))[0].name, 'report.html');
    // Change one, remove one.
    await writeFile(path.join(f.docs, 'recipe.md'), 'Cake recipe with chocolate.');
    await utimes(path.join(f.docs, 'recipe.md'), new Date(), new Date(Date.now() + 5000));
    await rm(path.join(f.docs, 'Work', 'report.html'));
    first = f.fake.stats().requests;
    await f.service.runOnce(AbortSignal.timeout(10_000));
    status = await f.index.status();
    assert.equal(status.files, 2);
    assert.equal(f.fake.stats().requests, first + 1, 'only the changed file is embedded');
    assert.equal((await f.index.search('chocolate cake', 1, AbortSignal.timeout(5000)))[0].name, 'recipe.md');
  } finally { await f.close(); }
});

test('name catalog: exact name, invoice number and company words, case/diacritic-insensitive', () => {
  const name = 'Colak-Health-Management-Ltd_Invoice_MLP-HEALTH-UK-014.pdf';
  assert.equal(normalizeName('Çolak_İŞ-ğün.PDF'), 'colak is gun pdf');
  assert.equal(nameScore(name, name), 1);
  assert.equal(nameScore('MLP-HEALTH-UK-014', name), 1);
  assert.equal(nameScore('uk014', name), 1);
  assert.equal(nameScore('Colak Health invoice', name), 1);
  assert.equal(nameScore('çolak health fatura', name) > 0.5, true);
  assert.equal(nameScore('rental contract', name), 0);
  assert.equal(nameScore('uk', name), 0, 'short tokens alone do not match');
});

test('embedding order: shallow documents first (newest first), then media, then the rest', () => {
  const e = (file: string, depth: number, mtime: number): [string, Found] => [file, { depth, mtime, size: 1 }];
  const list = [e('x.bin', 0, 9), e('a.jpg', 0, 5), e('deep.pdf', 3, 9), e('old.pdf', 0, 1), e('new.docx', 0, 8), e('b.mp4', 2, 7)];
  assert.deepEqual(list.sort(priority).map(x => x[0]), ['new.docx', 'old.pdf', 'deep.pdf', 'b.mp4', 'a.jpg', 'x.bin']);
});

test('walk skips developer folders and code repositories unless they are roots', async () => {
  const f = await setup();
  try {
    assert.deepEqual(validateSkipDirs([' build ', 'build', 'Pods']), ['build', 'Pods']);
    for (const bad of [['a/b'], [''], ['..'], 'x']) assert.throws(() => validateSkipDirs(bad));
    for (const dir of ['build', 'models', 'Pods', 'venv', 'Repo/.git', 'Repo/src', 'Notes'])
      await mkdir(path.join(f.docs, dir), { recursive: true });
    for (const file of ['build/a.txt', 'models/b.txt', 'Pods/c.txt', 'venv/d.txt', 'Repo/readme.md', 'Repo/src/x.ts', 'Notes/keep.txt', 'top.pdf'])
      await writeFile(path.join(f.docs, file), 'x');
    const names = async () => [...(await f.index.walk(AbortSignal.timeout(5000))).keys()].map(p => path.relative(f.docs, p)).sort();
    assert.deepEqual(await names(), ['Notes/keep.txt', 'top.pdf']);
    f.config.file_roots = [f.docs, path.join(f.docs, 'Repo')];
    f.config.file_skip_dirs = ['models'];
    assert.deepEqual(await names(), ['Notes/keep.txt', 'Pods/c.txt', 'Repo/readme.md', 'Repo/src/x.ts', 'build/a.txt', 'top.pdf', 'venv/d.txt']);
  } finally { await f.close(); }
});

test('a file is found by name right after the walk, even without embeddings', async () => {
  const f = await setup();
  try {
    const name = 'Colak-Health-Management-Ltd_Invoice_MLP-HEALTH-UK-014.pdf';
    await writeFile(path.join(f.docs, name), 'not a real pdf');
    await writeFile(path.join(f.docs, 'notes.txt'), 'shopping list milk');
    await f.fake.close(); // Embedding server down: nothing gets embedded.
    await f.service.runOnce(AbortSignal.timeout(10_000)).catch(() => {});
    const status = await f.index.status();
    assert.equal(status.files, 0); assert.equal(status.found, 2);
    for (const query of [name, 'MLP-HEALTH-UK-014', 'Colak Health invoice', 'çolak health'])
      assert.equal((await f.index.search(query, 3, AbortSignal.timeout(5000)))[0]?.name, name, query);
  } finally { await f.close(); }
});

test('name and meaning are merged: a name match ranks first, content hits follow', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.docs, 'Invoice_ACME-2024-117.txt'), 'payment for consulting services');
    await writeFile(path.join(f.docs, 'other.txt'), 'acme invoice 117 reminder letter');
    await writeFile(path.join(f.docs, 'Setup_Tool.exe'), Buffer.from([0, 1, 2]));
    await f.service.runOnce(AbortSignal.timeout(10_000));
    assert.deepEqual([(await f.index.status()).files, (await f.index.status()).found], [2, 3], 'unknown types: name catalog only');
    assert.equal((await f.index.search('setup tool', 1, AbortSignal.timeout(5000)))[0].name, 'Setup_Tool.exe');
    const hits = await f.index.search('ACME-2024-117', 2, AbortSignal.timeout(5000));
    assert.equal(hits[0].name, 'Invoice_ACME-2024-117.txt'); assert.equal(hits[0].score, 1);
    assert.ok(hits[0].snippet.includes('consulting'));
  } finally { await f.close(); }
});

test('an aborted pass resumes where it stopped; finished files are not embedded again', async () => {
  const f = await setup();
  try {
    for (let i = 0; i < 5; i++) await writeFile(path.join(f.docs, `doc${i}.txt`), `document number ${i}`);
    const controller = new AbortController();
    const embed = f.service.embedder.embed.bind(f.service.embedder);
    let calls = 0;
    f.service.embedder.embed = async (inputs, signal) => { const v = await embed(inputs, signal); if (++calls === 2) controller.abort(); return v; };
    await assert.rejects(f.service.runOnce(controller.signal));
    assert.equal((await f.index.status()).files, 2);
    assert.equal((await f.index.status()).found, 5);
    await f.service.runOnce(AbortSignal.timeout(10_000));
    assert.equal((await f.index.status()).files, 5);
    assert.equal(calls, 5, 'each file embedded once');
  } finally { await f.close(); }
});

test('the schedule retries a failed pass soon instead of waiting the full interval', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.docs, 'a.txt'), 'alpha');
    f.config.file_index_interval_ms = 3_600_000;
    let runs = 0;
    const run = f.index.run.bind(f.index);
    f.index.run = (signal, busy, full) => { if (++runs === 1) return Promise.reject(Error('embedding down')); return run(signal, busy, full); };
    const lifecycle = new AbortController();
    f.service.start(lifecycle.signal, 0, 50);
    for (let i = 0; i < 100 && (await f.index.status()).files < 1; i++) await new Promise(r => setTimeout(r, 20));
    assert.equal((await f.index.status()).files, 1); assert.equal(runs, 2);
    lifecycle.abort();
  } finally { await f.close(); }
});

test('indexing waits while a user request runs', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.docs, 'a.txt'), 'alpha');
    let busy = true;
    f.service.busy = () => busy;
    const run = f.service.runOnce(AbortSignal.timeout(10_000));
    await new Promise(r => setTimeout(r, 300));
    assert.equal(f.fake.stats().requests, 0);
    busy = false; await run;
    assert.equal((await f.index.status()).files, 1);
  } finally { await f.close(); }
});

test('media files are embedded by content when the projector is on, by card only when off', async () => {
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
  for (const mmproj of [true, false]) {
    const f = await setup({ mmproj });
    try {
      await writeFile(path.join(f.docs, 'IMG_0001.png'), png);
      await f.service.runOnce(AbortSignal.timeout(20_000));
      const hits = await f.index.search('picture', 1, AbortSignal.timeout(5000));
      assert.equal(hits[0].name, 'IMG_0001.png');
      assert.equal(hits[0].snippet === '[image]', mmproj);
    } finally { await f.close(); }
  }
});

test('file_read stays inside the roots: traversal, symlinks and hidden files are blocked', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.docs, 'long.txt'), Array.from({ length: 12 }, (_, i) => `Paragraph ${i} ` + 'word '.repeat(250)).join('\n\n'));
    await writeFile(path.join(f.dir, 'secret.txt'), 'top secret');
    await symlink(path.join(f.dir, 'secret.txt'), path.join(f.docs, 'link.txt'));
    await writeFile(path.join(f.docs, '.env'), 'KEY=1');
    const signal = AbortSignal.timeout(5000);
    const part = await f.index.read(path.join(f.docs, 'long.txt'), 0, signal);
    assert.ok((part.text as string).length <= 4000 && (part.parts as number) > 1);
    const later = await f.index.read(path.join(f.docs, 'long.txt'), 4, signal);
    assert.equal(later.part, 2);
    for (const bad of [path.join(f.docs, '..', 'secret.txt'), path.join(f.docs, 'link.txt'), path.join(f.docs, '.env'), '/etc/passwd', '../../etc/passwd'])
      await assert.rejects(f.index.read(bad, 0, signal), (e: Error & { code?: string }) => ['outside_roots', 'not_found'].includes(e.code ?? ''), bad);
  } finally { await f.close(); }
});

test('organize: propose → next-turn apply moves without overwrite or delete; undo restores', async () => {
  const f = await setup();
  try {
    await mkdir(path.join(f.docs, 'Invoices'));
    await writeFile(path.join(f.docs, 'Invoices', 'old-invoice.txt'), 'invoice payment amount due electricity bill');
    await writeFile(path.join(f.docs, 'invoice-march.txt'), 'invoice payment amount due water bill');
    await writeFile(path.join(f.docs, 'old-invoice.txt'), 'invoice payment amount due electricity bill copy');
    await writeFile(path.join(f.docs, 'trip-rome.txt'), 'holiday travel rome flight hotel booking');
    await writeFile(path.join(f.docs, 'trip-paris.txt'), 'holiday travel paris flight hotel booking');
    await writeFile(path.join(f.docs, 'misc.txt'), 'zebra quantum violin');
    await f.service.runOnce(AbortSignal.timeout(10_000));
    const memory = new FilesMemory(new ConfirmationStore());
    let named = '';
    const model = async (_s: string, user: string) => { named = user; return '{"1":"Seyahat/../x"}'; };
    const organizer = (turn: number) => new FileOrganizer({ index: f.index, memory, turn, model, logFile: path.join(f.dir, 'data/file-actions.jsonl'), threshold: 0.5 });
    const before = await readdir(f.docs);
    const plan = await organizer(10).propose(AbortSignal.timeout(5000));
    assert.equal(plan.files, 5); assert.equal(plan.to_existing, 2); assert.equal(plan.to_new, 2); assert.equal(plan.left, 1);
    assert.match(named, /trip-/);
    assert.ok(Array.isArray(plan.groups) && (plan.groups as string[]).length <= 5);
    assert.deepEqual(await readdir(f.docs), before, 'propose changes nothing');
    const token = plan.confirm_token as string;
    await assert.rejects(organizer(10).apply(token, AbortSignal.timeout(5000)), { code: 'confirmation_needs_reply' });
    await assert.rejects(organizer(11).apply('c_wrong', AbortSignal.timeout(5000)), { code: 'confirmation_invalid' });
    const result = await organizer(11).apply(token, AbortSignal.timeout(5000));
    assert.deepEqual(result, { moved: 4, skipped: 0, folders_created: 1 });
    await assert.rejects(organizer(11).apply(token, AbortSignal.timeout(5000)), { code: 'confirmation_invalid' }, 'single use');
    const invoices = (await readdir(path.join(f.docs, 'Invoices'))).sort();
    assert.deepEqual(invoices, ['invoice-march.txt', 'old-invoice (2).txt', 'old-invoice.txt'], 'name clash gets a suffix, nothing overwritten');
    assert.equal(await readFile(path.join(f.docs, 'Invoices', 'old-invoice.txt'), 'utf8'), 'invoice payment amount due electricity bill');
    assert.deepEqual((await readdir(path.join(f.docs, 'Seyahat x'))).sort(), ['trip-paris.txt', 'trip-rome.txt'], 'model name is one safe segment');
    const log = (await readFile(path.join(f.dir, 'data/file-actions.jsonl'), 'utf8')).trim().split('\n').map(l => JSON.parse(l));
    assert.equal(log.filter(l => l.kind === 'move').length, 4);
    // Moved files stay searchable under the new path without re-embedding.
    assert.match((await f.index.search('rome holiday', 1, AbortSignal.timeout(5000)))[0].path, /Seyahat x/);
    const undo = await organizer(12).undo(AbortSignal.timeout(5000));
    assert.deepEqual(undo, { restored: 4, skipped: 0 });
    assert.deepEqual((await readdir(f.docs)).sort(), before.sort());
    assert.equal(await readFile(path.join(f.docs, 'old-invoice.txt'), 'utf8'), 'invoice payment amount due electricity bill copy');
    await assert.rejects(organizer(13).undo(AbortSignal.timeout(5000)), { code: 'nothing_to_undo' });
  } finally { await f.close(); }
});

test('organize: a file changed after propose is skipped; tokens expire', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.docs, 'trip-a.txt'), 'holiday travel flight hotel booking one');
    await writeFile(path.join(f.docs, 'trip-b.txt'), 'holiday travel flight hotel booking two');
    await f.service.runOnce(AbortSignal.timeout(10_000));
    let now = 1_000_000;
    const memory = new FilesMemory(new ConfirmationStore(() => now));
    const organizer = (turn: number) => new FileOrganizer({ index: f.index, memory, turn, logFile: null, threshold: 0.5, now: () => now });
    let plan = await organizer(1).propose(AbortSignal.timeout(5000));
    await writeFile(path.join(f.docs, 'trip-a.txt'), 'changed');
    assert.deepEqual(await organizer(2).apply(plan.confirm_token as string, AbortSignal.timeout(5000)), { moved: 1, skipped: 1, folders_created: 1 });
    await writeFile(path.join(f.docs, 'trip-c.txt'), 'holiday travel flight hotel booking three');
    await writeFile(path.join(f.docs, 'trip-d.txt'), 'holiday travel flight hotel booking four');
    await f.service.runOnce(AbortSignal.timeout(10_000));
    plan = await organizer(3).propose(AbortSignal.timeout(5000));
    now += 3 * 60_000;
    await assert.rejects(organizer(4).apply(plan.confirm_token as string, AbortSignal.timeout(5000)), { code: 'confirmation_expired' });
    assert.ok((await stat(path.join(f.docs, 'trip-c.txt'))).isFile());
  } finally { await f.close(); }
});

test('clustering and folder names', () => {
  const v = (a: number, b: number) => new Float32Array([a, b]);
  assert.deepEqual(cluster([v(1, 0), v(0.99, 0.14), v(0, 1)], 0.9).map(g => g.sort()), [[0, 1], [2]]);
  assert.equal(folderName('../../etc'), 'etc'); assert.equal(folderName('.hidden'), 'hidden'); assert.equal(folderName('a/b:c'), 'a b c');
  assert.equal(folderName(42), ''); assert.equal(folderName('x'.repeat(80)).length, 40);
});

test('file tools are absent when the index is off and offered (offline too) when on', () => {
  assert.ok(!harnessTools(undefined, true).some(t => t.startsWith('file_')));
  assert.deepEqual(harnessTools('file-assistant', true, false, false, false), []);
  assert.deepEqual(harnessTools('file-assistant', true, false, false, true), ['file_search', 'file_read', 'file_organize']);
  assert.ok(harnessTools(undefined, true, false, false, true).includes('file_search'));
});

test('file tools: local model only (harness gate and permissions defence in depth)', async () => {
  const on = { ...defaults, embedding_enabled: true };
  assert.equal(filesReady(on, { provider: 'local-gemma' } as never), true);
  assert.equal(filesReady(on, { provider: 'openai-codex' } as never), false);
  assert.equal(filesReady(defaults, { provider: 'local-gemma' } as never), false);
  const decide = async (local: boolean | undefined, toolName: string) => {
    let handler: ((event: unknown) => unknown) | undefined;
    permissionsExtension({ root: tmpdir(), offline: false, local })(
      { on: (name: string, fn: (event: unknown) => unknown) => { if (name === 'tool_call') handler = fn; } } as unknown as ExtensionAPI);
    return await handler!({ toolName, input: {} }) as { block?: boolean } | undefined;
  };
  for (const tool of ['file_search', 'file_read', 'file_organize']) {
    assert.equal((await decide(false, tool))?.block, true, tool);
    assert.equal((await decide(undefined, tool))?.block, true, tool);
    assert.equal(await decide(true, tool), undefined, tool);
  }
});

test('admin: file switch and folders are saved to config.json; off stops the server', async () => {
  const f = await setup();
  try {
    const { saveFiles, filesStatus } = await import('../src/admin.ts');
    const configPath = path.join(f.dir, 'config.json');
    await writeFile(configPath, JSON.stringify({ language: 'tr' }));
    let closed = 0; const close = f.service.embedder.close.bind(f.service.embedder);
    f.service.embedder.close = async () => { closed++; await close(); };
    const saved = await saveFiles(f.config, configPath, f.service, { roots: [f.docs, '~/Pictures'] });
    assert.deepEqual(saved.roots, [f.docs, '~/Pictures']);
    await assert.rejects(saveFiles(f.config, configPath, f.service, { roots: ['/'] }));
    const off = await saveFiles(f.config, configPath, f.service, { enabled: false });
    assert.equal(off.enabled, false); assert.equal(off.index, null); assert.equal(closed, 1);
    assert.deepEqual(JSON.parse(await readFile(configPath, 'utf8')), { language: 'tr', file_roots: [f.docs, '~/Pictures'], embedding_enabled: false });
    assert.equal((await filesStatus(f.config, f.service)).enabled, false);
  } finally { await f.close(); }
});
