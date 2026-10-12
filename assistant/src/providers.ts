import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { createModels, createProvider, type Context, type Model, type Models, type AssistantMessage } from '@earendil-works/pi-ai';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { typesafeProvider } from '@earendil-works/pi-ai/providers/typesafe';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { FileCredentials } from './credentials.ts';
import type { Config } from './config.ts';

export type HarnessSkill = 'online-search' | 'skill-manager' | 'mail-assistant' | 'reminders' | 'mail-triage' | 'file-assistant';
export interface Chat { complete(context: Context, signal: AbortSignal, options?: { offline: boolean; skill?: HarnessSkill; briefNews?: boolean; turn?: number; automated?: boolean; steering?: Steering }): Promise<AssistantMessage>; }

/** Barge-in: extra user text for a request that is still generating. Accepted only while a live Pi session
 *  runs the request (it is injected before the next model call; one reply). Otherwise `steer` returns false. */
export class Steering {
  /** Every accepted text, in order (conversation history and a local fallback include them). */
  readonly accepted: string[] = [];
  private deliver?: (text: string) => boolean;
  attach(deliver: (text: string) => boolean): void { this.deliver = deliver; }
  detach(): void { this.deliver = undefined; }
  steer(text: string): boolean {
    text = text.trim();
    if (!text || !this.deliver || !this.deliver(text)) return false;
    this.accepted.push(text); return true;
  }
}
export interface Intent { choice: string; confidence: number; provider: string; }
export interface Classifier { classify(message: string, signal: AbortSignal): Promise<Intent>; }

export function createCollection(config: Config): Models {
  const models = createModels({ credentials: new FileCredentials(path.join(config.pi_agent_dir, 'auth.json')) });
  models.setProvider(openaiCodexProvider());
  models.setProvider(openaiProvider()); // Official Sign in with ChatGPT route for a fresh app-owned login.
  models.setProvider(typesafeProvider());
  const local: Model<'openai-completions'> = {
    id: config.local_model, name: 'Local Gemma', provider: 'local-gemma', api: 'openai-completions',
    baseUrl: config.local_url, reasoning: false, input: ['text'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: config.local_context, maxTokens: config.max_tokens,
    compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false }
  };
  models.setProvider(createProvider({ id: 'local-gemma', models: [local], api: openAICompletionsApi(),
    auth: { apiKey: { name: 'Local endpoint', resolve: async () => ({ auth: { apiKey: process.env.ASSISTANT_LOCAL_API_KEY || 'local' } }) } }
  }));
  return models;
}

export class PiChat implements Chat {
  models: Models; config: Config; local: boolean;
  constructor(models: Models, config: Config, local = false) { this.models = models; this.config = config; this.local = local; }
  async resolveModel(): Promise<Model<any>> {
    const c = this.config;
    let provider = this.local ? 'local-gemma' : 'openai-codex';
    let modelId = this.local ? c.local_model : c.codex_model;
    if (!this.local) {
      const metadata = await new FileCredentials(path.join(c.pi_agent_dir, 'auth.json')).list();
      // Require OAuth: never fall through to a separately billed ambient API key.
      if (metadata.some(m => m.providerId === 'openai' && m.type === 'oauth')) provider = 'openai';
      else if (!metadata.some(m => m.providerId === 'openai-codex' && m.type === 'oauth'))
        throw Error('Subscription sign-in required: use global pi /login');
      if (!modelId) {
        let settings: { defaultModel?: string; defaultProvider?: string } = {};
        try { settings = JSON.parse(await readFile(path.join(c.pi_agent_dir, 'settings.json'), 'utf8')); } catch {}
        if (settings.defaultProvider === provider && settings.defaultModel) modelId = settings.defaultModel;
        else throw Error('Set codex_model or select a subscription model in global Pi');
      }
    }
    const model = this.models.getModel(provider, modelId);
    if (!model) throw Error('Configured model is absent from Pi catalog; run models command');
    return model;
  }
  async complete(context: Context, signal: AbortSignal): Promise<AssistantMessage> {
    const c = this.config;
    const model = await this.resolveModel();
    const result = await this.models.completeSimple(model, context, {
      signal, transport: 'sse',
      ...(this.local ? { maxTokens: c.max_tokens, temperature: 0.3 } : { reasoning: 'low' }),
    });
    if (result.stopReason === 'error' || result.stopReason === 'aborted')
      throw Error(this.local ? 'Local model request failed' : 'Codex request failed or timed out; check sign-in, model access, and usage limits');
    if (!result.content.some(b => b.type === 'text' && b.text.trim())) throw Error('Model returned no usable text reply');
    return result;
  }
}

export class Jev implements Classifier {
  models: Models; config: Config;
  constructor(models: Models, config: Config) { this.models = models; this.config = config; }
  async classify(message: string, signal: AbortSignal): Promise<Intent> {
    const model = this.models.getModelOfType('classifier', 'typesafe', this.config.jev_model);
    if (!model) throw Error('Jev model missing from Pi catalog');
    const result = await this.models.classify(model, { state: { message }, questions: {
      intent: { type: 'choice', instructions: 'Classify the user request by the main capability it needs.', criteria: {
        conversation: 'General chat, reasoning, writing, questions, or advice',
        email: 'Read, search, summarize, draft, or send email (no changes to existing mail)',
        mail_triage: 'Change existing Gmail mail: delete/trash, archive, label, mark read, star, sort by importance, clean up or undo, also for specific senders or subjects (mail sil, düzenle, temizle)',
        calendar: 'Read or modify calendar events, availability, meetings, or appointments',
        reminders: 'Add, list, update, complete or delete reminders or to-dos (hatırlatıcı, yapılacaklar)',
        files: "Find, read, answer from or organize the user's own files, documents, photos, recordings or folders on this computer (dosya, belge, klasör, fotoğraf bul/düzenle)",
        news: 'Brief latest/recent news, headlines or a short news roundup about a place/topic, including Turkish haber/haberler requests; use web_search for requested detailed investigation',
        web_search: 'General online research, current facts, or detailed investigation needing public web verification; use news for brief headlines',
        skill_management: 'Create, edit, inspect, test or improve the assistant harness skills or extensions'
      } }
    } }, { signal });
    const answer = result.answers.intent;
    if (result.stopReason !== 'stop' || answer?.type !== 'choice' ||
        !['conversation', 'email', 'mail_triage', 'calendar', 'reminders', 'files', 'news', 'web_search', 'skill_management'].includes(answer.choice) ||
        !Number.isFinite(answer.confidence) || answer.confidence < 0 || answer.confidence > 1)
      throw Error('Jev classification unavailable');
    return { choice: answer.choice, confidence: answer.confidence, provider: 'jev' };
  }
}
