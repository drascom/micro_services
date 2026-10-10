import { mkdtemp, writeFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { ChildProcess } from 'node:child_process';
import { runProcess, ownedProcess, type OwnedProcess } from './processes.ts';
import type { Config } from './config.ts';
import { spokenText } from './spoken-text.ts';
import { activity } from './activity.ts';
import { EmaWorker } from './ema-worker.ts';
import { KokoroWorker } from './kokoro-worker.ts';
import { WhisperServer, whisperArgs } from './whisper.ts';

export class NoSpeechError extends Error {
  constructor(message = 'No speech detected') { super(message); this.name = 'NoSpeechError'; }
}

const patterns = ['altyazı m.k.', 'altyazı m.k', 'altyazı mk', 'altyazi mk', 'altyazı', 'türkçe altyazı',
  'abone olmayı unutmayın', 'abone ol', 'abone olun', 'kanalıma abone olun', 'beni izlediğiniz için teşekkürler',
  'izlediğiniz için teşekkür ederim', 'izlediğiniz için teşekkürler', 'bir sonraki videoda görüşmek üzere',
  'altyazı ve alt yazı m.k.', 'thank you', 'thanks for watching', 'please subscribe', 'subtitles by',
  'bye', 'you', '[blank_audio]', '[music]', '[müzik]', '(müzik)'];
export function normalize(text: string): string {
  return text.replace(/I/g, 'ı').replace(/İ/g, 'i').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}
export function guardText(text: string, duration: number, minimum: number): string {
  const normalized = normalize(text);
  if (duration < minimum || !normalized) throw new NoSpeechError('No speech or recording too short');
  if (text.length <= 40 && (/^\[.*\]$/.test(text) || /^\(.*\)$/.test(text))) throw new NoSpeechError('Audio tag instead of speech');
  let residue = ' ' + normalized + ' ';
  for (const pattern of patterns.map(normalize).sort((a, b) => b.length - a.length)) {
    while (residue.includes(' ' + pattern + ' ')) residue = residue.replace(' ' + pattern + ' ', '  ');
  }
  if (!residue.trim()) throw new NoSpeechError('Whisper silence boilerplate detected');
  const words = normalized.split(' ');
  if (words.length >= 6 && new Set(words).size === 1) throw new NoSpeechError('Repeated-word hallucination detected');
  return text.trim();
}

export function wavDuration(bytes: Buffer): number {
  if (bytes.length < 44 || bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE')
    throw Error('Invalid WAV audio');
  if (bytes.readUInt32LE(4) + 8 !== bytes.length) throw Error('Truncated or inconsistent WAV');
  let format = false, dataLength = 0;
  for (let at = 12; at + 8 <= bytes.length;) {
    const tag = bytes.toString('ascii', at, at + 4), length = bytes.readUInt32LE(at + 4), start = at + 8;
    if (start + length > bytes.length) throw Error('Truncated WAV chunk');
    if (tag === 'fmt ') {
      if (length < 16 || bytes.readUInt16LE(start) !== 1 || bytes.readUInt16LE(start + 2) !== 1 ||
          bytes.readUInt32LE(start + 4) !== 16000 || bytes.readUInt16LE(start + 12) !== 2 ||
          bytes.readUInt16LE(start + 14) !== 16) throw Error('Use 16 kHz mono PCM16 WAV');
      format = true;
    }
    if (tag === 'data') dataLength += length;
    at = start + length + (length % 2);
  }
  if (format && !dataLength) throw new NoSpeechError();
  if (!format || dataLength % 2) throw Error('WAV has no valid PCM data');
  return dataLength / 32000;
}

export interface SpeechService { transcribe(bytes: Buffer, signal: AbortSignal): Promise<string>; speak(text: string, signal: AbortSignal): Promise<void>; stop(): void; pause?(): void; resume?(): void; close(): Promise<void>; processes?(): OwnedProcess[]; }
export class Speech implements SpeechService {
  config: Config; speaker?: AbortController; player?: ChildProcess; paused = false; ema: EmaWorker; kokoro: KokoroWorker; whisper: WhisperServer;
  constructor(config: Config) {
    this.config = config; this.ema = new EmaWorker(config); this.kokoro = new KokoroWorker(config);
    this.whisper = new WhisperServer(config);
  }
  async transcribe(bytes: Buffer, signal: AbortSignal): Promise<string> {
    const c = this.config, duration = wavDuration(bytes);
    if (duration < c.min_record_seconds) throw new NoSpeechError('Recording too short');
    if (duration > c.max_record_seconds) throw Error('Recording duration outside limits');
    const limit = AbortSignal.any([signal, AbortSignal.timeout(c.whisper_timeout_ms)]);
    let text: string | undefined;
    if (c.whisper_server) {
      try { text = await this.whisper.transcribe(bytes, limit); }
      catch (error) {
        limit.throwIfAborted();
        // One controlled fallback per request: drop the (possibly broken) server, use the per-request CLI.
        activity('stt_fallback', { from: 'whisper-server', to: 'whisper-cli', error: error instanceof Error ? error.message : 'unknown' });
        await this.whisper.close();
      }
    }
    text ??= await this.transcribeCli(bytes, limit);
    return guardText(text.replace(/\s+/g, ' ').trim(), duration, c.min_record_seconds);
  }
  async transcribeCli(bytes: Buffer, signal: AbortSignal): Promise<string> {
    const c = this.config;
    await access(c.whisper_bin); await access(c.whisper_model);
    const directory = await mkdtemp(path.join(tmpdir(), 'assistant-audio-'));
    try {
      const wav = path.join(directory, 'speech.wav'); await writeFile(wav, bytes, { mode: 0o600 });
      return await runProcess(c.whisper_bin, [...whisperArgs(c), '-np', '-f', wav], signal);
    } finally { await rm(directory, { recursive: true, force: true }); }
  }
  async speak(text: string, signal: AbortSignal): Promise<void> {
    this.stop();
    const originalLength = text.length;
    text = spokenText(text);
    activity('speech_prepared', { original_chars: originalLength, spoken_chars: text.length });
    if (!text) return;
    const controller = new AbortController(); this.speaker = controller;
    try {
      const speechSignal = AbortSignal.any([signal, controller.signal, AbortSignal.timeout(300000)]);
      const language = (await runProcess(this.config.language_detector, [], speechSignal, text)).trim();
      const piper = async () => {
        const model = language === 'tr' ? this.config.piper_model_tr : this.config.piper_model_en;
        const speed = language === 'tr' ? this.config.piper_speed_tr : this.config.piper_speed_en;
        await access(this.config.piper_bin); await access(model); await access(model + '.json');
        const directory = await mkdtemp(path.join(tmpdir(), 'assistant-speech-'));
        try {
          const wav = path.join(directory, 'reply.wav');
          await runProcess(this.config.piper_bin, ['-m', 'piper', '-m', model, '-f', wav,
            '--length-scale', String(1 / speed)], speechSignal, text);
          await this.play('/usr/bin/afplay', [wav], speechSignal);
        } finally { await rm(directory, { recursive: true, force: true }); }
      };
      if (this.config.tts_engine === 'ema' && language === 'tr') {
        const directory = await mkdtemp(path.join(tmpdir(), 'assistant-ema-'));
        try {
          const wav = path.join(directory, 'reply.wav');
          try { await this.ema.synthesize(text, wav, speechSignal); }
          catch (error) {
            speechSignal.throwIfAborted();
            activity('tts_fallback', { from: 'ema', to: 'piper', error: error instanceof Error ? error.message : 'unknown' });
            await piper(); return;
          }
          await this.play('/usr/bin/afplay', [wav], speechSignal);
        } finally { await rm(directory, { recursive: true, force: true }); }
      } else if (this.config.tts_engine === 'ema') {
        const directory = await mkdtemp(path.join(tmpdir(), 'assistant-kokoro-'));
        try {
          const wav = path.join(directory, 'reply.wav');
          try { await this.kokoro.synthesize(text, wav, speechSignal); }
          catch (error) {
            speechSignal.throwIfAborted();
            activity('tts_fallback', { from: 'kokoro', to: 'piper', error: error instanceof Error ? error.message : 'unknown' });
            await piper(); return;
          }
          await this.play('/usr/bin/afplay', [wav], speechSignal);
        } finally { await rm(directory, { recursive: true, force: true }); }
      } else if (this.config.tts_engine === 'piper') {
        await piper();
      } else {
        const voice = this.config.voice || (language === 'tr' ? this.config.voice_tr : this.config.voice_en);
        await this.play('/usr/bin/say', ['-f', '-', ...(voice ? ['-v', voice] : [])], speechSignal, text);
      }
    }
    catch (error) { if (!controller.signal.aborted) throw error; }
    finally { if (this.speaker === controller) this.speaker = undefined; }
  }
  /** Audio output process; held (SIGSTOP) while the user records, continued afterwards. */
  async play(bin: string, args: string[], signal: AbortSignal, input?: string): Promise<void> {
    try { await runProcess(bin, args, signal, input, child => { this.player = child; if (this.paused) child.kill('SIGSTOP'); }); }
    finally { this.player = undefined; }
  }
  pause(): void { this.paused = true; this.player?.kill('SIGSTOP'); }
  resume(): void { this.paused = false; this.player?.kill('SIGCONT'); }
  stop(): void { this.resume(); this.speaker?.abort(); }
  processes(): OwnedProcess[] {
    return [ownedProcess('whisper-server', this.whisper.child, Number(new URL(this.config.whisper_url).port)),
      ownedProcess('ema-worker', this.ema.child), ownedProcess('kokoro-worker', this.kokoro.child)];
  }
  async close(): Promise<void> { this.stop(); await Promise.all([this.ema.close(), this.kokoro.close(), this.whisper.close()]); }
}
