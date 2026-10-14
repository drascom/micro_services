import { readFile, access } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { loadConfig, loadEnv, root } from './config.ts';
import { observeRequest } from './activity.ts';
import { createCollection, Jev } from './providers.ts';
import { PiHarnessChat } from './harness.ts';
import { FileCredentials } from './credentials.ts';
import { LocalRuntime } from './processes.ts';
import { Speech } from './speech.ts';
import { Orchestrator } from './orchestrator.ts';
import { makeServer } from './server.ts';
import { fileService } from './files.ts';
import { vaultService } from './vault.ts';
import { installedVersion, loadManifest } from './components.ts';
import { detect, httpJson } from './updates.ts';

export async function main(args = process.argv.slice(2)) {
  await loadEnv(); const config = await loadConfig();
  const models = createCollection(config), runtime = new LocalRuntime(config), speech = new Speech(config);
  const app = new Orchestrator(config, new PiHarnessChat(models, config), new PiHarnessChat(models, config, true), runtime, speech,
    process.env.TYPESAFE_API_KEY ? new Jev(models, config) : undefined);
  const lifecycle = new AbortController();
  const abort = () => lifecycle.abort();
  process.on('SIGINT', abort); process.on('SIGTERM', abort);
  try {
    const command = args.shift() || 'serve';
    if (command === 'doctor') {
      const exists = async (p: string, executable = false) => { try { await access(p, executable ? constants.X_OK : constants.F_OK); return true; } catch { return false; } };
      console.log(JSON.stringify({
        whisper_binary: await exists(config.whisper_bin, true), whisper_model: await exists(config.whisper_model),
        whisper_server: config.whisper_server, whisper_server_binary: await exists(config.whisper_server_bin, true),
        whisper_server_running: await speech.whisper.healthy(),
        llama_binary: await exists(config.llama_bin, true), llama_model: await exists(config.llama_model),
        tts_engine: config.tts_engine, ema_python: await exists(config.ema_python, true),
        ema_worker: await exists(config.ema_worker), ema_cache: config.ema_cache, ema_device: config.ema_device,
        kokoro_python: await exists(config.kokoro_python, true), kokoro_worker: await exists(config.kokoro_worker),
        kokoro_model: await exists(config.kokoro_model), kokoro_voices: await exists(config.kokoro_voices),
        kokoro_voice: config.kokoro_voice,
        piper_binary: await exists(config.piper_bin, true),
        piper_turkish_model: await exists(config.piper_model_tr), piper_english_model: await exists(config.piper_model_en),
        audio_player: await exists('/usr/bin/afplay'),
        pi_credentials: await new FileCredentials(path.join(config.pi_agent_dir, 'auth.json')).list(),
        jev_key_configured: Boolean(process.env.TYPESAFE_API_KEY), local_running: await runtime.healthy(),
        embedding_enabled: config.embedding_enabled, embedding_binary: await exists(config.embedding_bin, true),
        embedding_model: await exists(config.embedding_model), embedding_mmproj: config.embedding_mmproj ? await exists(config.embedding_mmproj) : false,
        // components.json entries whose installed version differs from the pin (scripts/setup.sh installs them).
        components_mismatch: await (async () => {
          const result: string[] = [];
          for (const c of (await loadManifest(path.join(root, 'components.json'))).components) {
            const installed = await installedVersion(c, root);
            if (installed !== c.version) result.push(`${c.id}: ${installed ?? 'missing'} != ${c.version}`);
          }
          return result;
        })(),
      }, null, 2));
    } else if (command === 'updates-check') {
      // Read-only: asks each upstream once and prints what is newer; nothing is installed or written. --all includes manual (models).
      const all = args.includes('--all'), rows = [];
      for (const c of (await loadManifest(path.join(root, 'components.json'))).components) {
        if ((!all && c.channel === 'manual') || !['github', 'npm', 'pypi', 'hf'].includes(c.source.type)) continue;
        const current = await installedVersion(c, root) ?? c.version;
        try {
          const up = await detect(c, current, httpJson, lifecycle.signal);
          rows.push({ id: c.id, channel: c.channel, current, latest: up?.latest ?? current, update: Boolean(up), ...(up?.sha256 ? { sha256: up.sha256 } : {}) });
        } catch (error) { rows.push({ id: c.id, channel: c.channel, current, error: error instanceof Error ? error.message : 'failed' }); }
      }
      console.log(JSON.stringify(rows, null, 1));
    } else if (command === 'models') {
      console.log(models.getModels().filter(m => ['openai', 'openai-codex'].includes(m.provider)).map(m => `${m.provider}/${m.id}`).join('\n'));
    } else if (command === 'login') {
      console.log('Use your global Pi connection: launch pi, then /login and select OpenAI.\nThis assistant shares that login and model preference.');
    } else if (command === 'ask') {
      const flags = new Set(args.filter(a => a.startsWith('--'))), prompt = args.filter(a => !a.startsWith('--')).join(' ');
      const answer = await observeRequest(path.join(root, 'data/activity.log'), { kind: 'chat', offline: flags.has('--offline'), text_chars: prompt.length }, () => app.ask(prompt, flags.has('--offline'), lifecycle.signal));
      console.log(JSON.stringify(answer));
      if (flags.has('--speak')) await speech.speak(answer.text, lifecycle.signal);
    } else if (command === 'classify') {
      if (!process.env.TYPESAFE_API_KEY) throw Error('Add TypeSafe token to env.txt');
      const result = await new Jev(models, config).classify(args.join(' '), AbortSignal.any([lifecycle.signal, AbortSignal.timeout(config.jev_timeout_ms)]));
      console.log(JSON.stringify(result));
    } else if (command === 'transcribe') {
      if (!args[0]) throw Error('Usage: ./run.sh transcribe /path/to/audio.wav');
      console.log(await speech.transcribe(await readFile(args[0]), lifecycle.signal));
    } else if (command === 'serve') {
      const files = fileService(config);
      files.start(lifecycle.signal);
      const server = await makeServer(config, app, lifecycle.signal, undefined, undefined, undefined, { files },
        { stateFile: path.join(root, 'data/tasks-state.json') }, { stateFile: path.join(root, 'data/updates.json') });
      await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(config.port, '127.0.0.1', resolve); });
      const url = `http://127.0.0.1:${config.port}`;
      console.log(`Assistant: ${url}`);
      await new Promise<void>(resolve => {
        const stop = () => { server.close(() => resolve()); server.closeIdleConnections(); };
        lifecycle.signal.addEventListener('abort', stop, { once: true });
        if (lifecycle.signal.aborted) stop();
      });
    } else throw Error('Commands: serve, ask, classify, transcribe, doctor, updates-check, models, login');
  } finally { await vaultService(config).close(); await app.close(); await fileService(config).close(); process.off('SIGINT', abort); process.off('SIGTERM', abort); }
}

if (import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch(error => { console.error(`Assistant: ${error instanceof Error ? error.message : 'Failed'}`); process.exitCode = 1; });
}
