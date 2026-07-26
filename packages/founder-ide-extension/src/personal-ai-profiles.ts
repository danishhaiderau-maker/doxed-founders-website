import { randomUUID } from 'node:crypto';
import type * as vscode from 'vscode';

export type PersonalAiProfileKind = 'openai-compatible' | 'ollama';

export interface PersonalAiProfileDraft {
  id?: string;
  name: string;
  kind: PersonalAiProfileKind;
  baseUrl: string;
  apiKey?: string;
  model: string;
  visionModel?: string;
  useForVisuals?: boolean;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface PersonalAiProfileSummary {
  id: string;
  name: string;
  kind: PersonalAiProfileKind;
  baseUrl: string;
  model: string;
  visionModel: string;
  useForVisuals: boolean;
  enabled: boolean;
  hasApiKey: boolean;
  headerNames: string[];
  createdAt: string;
  updatedAt: string;
}

export interface PersonalAiProfileSecret extends PersonalAiProfileSummary {
  apiKey: string;
  headers: Record<string, string>;
}

export interface PersonalAiProbeResult {
  ok: boolean;
  message: string;
  latencyMs: number;
}

export interface PersonalAiNativeBridge {
  save(profile: PersonalAiProfileSecret): Promise<void>;
  select(id: string | null): Promise<void>;
  setEnabled(id: string, enabled: boolean): Promise<void>;
  delete(id: string): Promise<void>;
}

const SECRET_KEY = 'founderOs.personalAiProfiles.v1';
const ACTIVE_PROFILE_KEY = 'founderOs.activePersonalAiProfile';
const MAX_HEADERS = 24;
const BLOCKED_HEADERS = new Set([
  'authorization',
  'connection',
  'content-length',
  'content-type',
  'host',
  'proxy-authorization',
  'transfer-encoding',
]);

function isPrivateIpv4(hostname: string): boolean {
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(hostname);
  if (!match) return false;
  const octets = match.slice(1).map(Number);
  if (octets.some((value) => value < 0 || value > 255)) return false;
  return octets[0] === 10
    || octets[0] === 127
    || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
    || (octets[0] === 192 && octets[1] === 168);
}

function isLocalEndpoint(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  return normalized === 'localhost'
    || normalized === '::1'
    || normalized.endsWith('.local')
    || isPrivateIpv4(normalized);
}

export function normalizePersonalAiBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, '');
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error('Enter a complete provider URL, including https://.');
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('Provider URLs cannot contain credentials, query parameters, or fragments.');
  }
  if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && isLocalEndpoint(parsed.hostname))) {
    throw new Error('Remote providers must use HTTPS. HTTP is allowed only for local or private-network models.');
  }
  const suffix = '/chat/completions';
  if (parsed.pathname.toLowerCase().endsWith(suffix)) {
    parsed.pathname = parsed.pathname.slice(0, -suffix.length) || '/';
  }
  return parsed.toString().replace(/\/+$/, '');
}

export function parsePersonalAiHeaders(value: unknown): Record<string, string> {
  if (value == null || value === '') return {};
  let source: unknown = value;
  if (typeof value === 'string') {
    try {
      source = JSON.parse(value);
    } catch {
      throw new Error('Optional headers must be a JSON object.');
    }
  }
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    throw new Error('Optional headers must be a JSON object.');
  }
  const entries = Object.entries(source as Record<string, unknown>);
  if (entries.length > MAX_HEADERS) {
    throw new Error(`Use no more than ${MAX_HEADERS} optional headers.`);
  }
  const headers: Record<string, string> = {};
  for (const [rawName, rawValue] of entries) {
    const name = rawName.trim();
    if (!/^[A-Za-z0-9-]{1,80}$/.test(name)) {
      throw new Error(`Invalid header name: ${rawName}`);
    }
    if (BLOCKED_HEADERS.has(name.toLowerCase())) {
      throw new Error(`${name} is managed by Founder and cannot be overridden.`);
    }
    if (typeof rawValue !== 'string' || rawValue.length > 2_000) {
      throw new Error(`${name} must have a string value shorter than 2,000 characters.`);
    }
    headers[name] = rawValue;
  }
  return headers;
}

export function validatePersonalAiProfile(
  draft: PersonalAiProfileDraft,
  existing?: PersonalAiProfileSecret,
): Omit<PersonalAiProfileSecret, 'id' | 'createdAt' | 'updatedAt' | 'hasApiKey' | 'headerNames'> {
  const name = draft.name.trim();
  const model = draft.model.trim();
  const visionModel = draft.visionModel?.trim() || model;
  if (!name || name.length > 60) throw new Error('Profile name must be 1-60 characters.');
  if (!model || model.length > 200) throw new Error('Model ID must be 1-200 characters.');
  if (visionModel.length > 200) throw new Error('Vision model ID must be 1-200 characters.');
  if (draft.kind !== 'openai-compatible' && draft.kind !== 'ollama') {
    throw new Error('Choose OpenAI-compatible or Ollama.');
  }
  const baseUrl = normalizePersonalAiBaseUrl(draft.baseUrl);
  const apiKey = draft.apiKey?.trim() || existing?.apiKey || '';
  if (draft.kind === 'openai-compatible' && !apiKey) {
    throw new Error('An API key is required for a remote OpenAI-compatible profile.');
  }
  const headers = draft.headers === undefined && existing
    ? existing.headers
    : parsePersonalAiHeaders(draft.headers);
  return {
    name,
    kind: draft.kind,
    baseUrl,
    apiKey,
    model,
    visionModel,
    useForVisuals: draft.useForVisuals ?? existing?.useForVisuals ?? false,
    headers,
    enabled: draft.enabled ?? existing?.enabled ?? true,
  };
}

export function personalAiApiBase(profile: Pick<PersonalAiProfileSecret, 'kind' | 'baseUrl'>): string {
  const base = profile.baseUrl.replace(/\/+$/, '');
  if (profile.kind !== 'ollama') return base;
  return /\/v1$/i.test(base) ? base : `${base}/v1`;
}

export function personalAiRequestHeaders(
  profile: Pick<PersonalAiProfileSecret, 'apiKey' | 'headers'>,
): Record<string, string> {
  return {
    ...profile.headers,
    ...(profile.apiKey ? { Authorization: `Bearer ${profile.apiKey}` } : {}),
  };
}

export async function probePersonalAiProfile(
  profile: PersonalAiProfileSecret,
  fetchImpl: typeof fetch = fetch,
): Promise<PersonalAiProbeResult> {
  const startedAt = Date.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  const url = profile.kind === 'ollama'
    ? `${profile.baseUrl.replace(/\/+$/, '')}/api/tags`
    : `${personalAiApiBase(profile)}/models`;
  try {
    const response = await fetchImpl(url, {
      method: 'GET',
      headers: personalAiRequestHeaders(profile),
      signal: controller.signal,
    });
    const latencyMs = Date.now() - startedAt;
    if (!response.ok) {
      return {
        ok: false,
        latencyMs,
        message: `Connection returned HTTP ${response.status}. Check the URL, key, and provider permissions.`,
      };
    }
    return {
      ok: true,
      latencyMs,
      message: `${profile.name} is reachable (${latencyMs} ms).`,
    };
  } catch (error) {
    const latencyMs = Date.now() - startedAt;
    const timedOut = controller.signal.aborted;
    return {
      ok: false,
      latencyMs,
      message: timedOut
        ? `${profile.name} did not respond within 10 seconds.`
        : `${profile.name} could not be reached: ${error instanceof Error ? error.message : String(error)}`,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function summary(profile: PersonalAiProfileSecret): PersonalAiProfileSummary {
  const { apiKey, headers, ...rest } = profile;
  return {
    ...rest,
    hasApiKey: Boolean(apiKey),
    headerNames: Object.keys(headers).sort((left, right) => left.localeCompare(right)),
  };
}

function isStoredProfile(value: unknown): value is PersonalAiProfileSecret {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<PersonalAiProfileSecret>;
  return typeof candidate.id === 'string'
    && typeof candidate.name === 'string'
    && (candidate.kind === 'openai-compatible' || candidate.kind === 'ollama')
    && typeof candidate.baseUrl === 'string'
    && typeof candidate.apiKey === 'string'
    && typeof candidate.model === 'string'
    && (candidate.visionModel == null || typeof candidate.visionModel === 'string')
    && (candidate.useForVisuals == null || typeof candidate.useForVisuals === 'boolean')
    && typeof candidate.enabled === 'boolean'
    && candidate.headers != null
    && typeof candidate.headers === 'object'
    && !Array.isArray(candidate.headers)
    && typeof candidate.createdAt === 'string'
    && typeof candidate.updatedAt === 'string';
}

export class PersonalAiProfileStore implements vscode.Disposable {
  private readonly listeners = new Set<() => unknown>();
  readonly onDidChange: vscode.Event<void> = (listener, thisArgs, disposables) => {
    const bound = () => listener.call(thisArgs);
    this.listeners.add(bound);
    const disposable = { dispose: () => this.listeners.delete(bound) };
    disposables?.push(disposable);
    return disposable;
  };
  private readonly loadPromise: Promise<void>;
  private profiles: PersonalAiProfileSecret[] = [];

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly nativeBridge?: PersonalAiNativeBridge,
  ) {
    this.loadPromise = this.load();
  }

  async ready(): Promise<void> {
    await this.loadPromise;
  }

  list(): PersonalAiProfileSummary[] {
    return this.profiles.map(summary);
  }

  activeId(): string | null {
    const id = this.context.globalState.get<string>(ACTIVE_PROFILE_KEY);
    return this.profiles.some((profile) => profile.id === id && profile.enabled) ? id! : null;
  }

  active(): PersonalAiProfileSecret | null {
    const id = this.activeId();
    const profile = this.profiles.find((candidate) => candidate.id === id);
    return profile ? { ...profile, headers: { ...profile.headers } } : null;
  }

  visual(): PersonalAiProfileSecret | null {
    const profile = this.profiles.find(
      (candidate) => candidate.enabled && candidate.useForVisuals,
    );
    return profile ? { ...profile, headers: { ...profile.headers } } : null;
  }

  get(id: string): PersonalAiProfileSecret | null {
    const profile = this.profiles.find((candidate) => candidate.id === id);
    return profile ? { ...profile, headers: { ...profile.headers } } : null;
  }

  async save(draft: PersonalAiProfileDraft): Promise<PersonalAiProfileSummary> {
    await this.ready();
    const existing = draft.id ? this.profiles.find((profile) => profile.id === draft.id) : undefined;
    const value = validatePersonalAiProfile(draft, existing);
    const now = new Date().toISOString();
    const next: PersonalAiProfileSecret = {
      ...value,
      id: existing?.id ?? randomUUID(),
      createdAt: existing?.createdAt ?? now,
      updatedAt: now,
      hasApiKey: Boolean(value.apiKey),
      headerNames: Object.keys(value.headers),
    };
    const profiles = existing
      ? this.profiles.map((profile) => profile.id === existing.id ? next : profile)
      : [...this.profiles, next];
    const priorVisualProfileIds = new Set(
      profiles
        .filter((profile) => profile.id !== next.id && profile.useForVisuals)
        .map((profile) => profile.id),
    );
    this.profiles = next.useForVisuals
      ? profiles.map((profile) => profile.id === next.id
        ? profile
        : { ...profile, useForVisuals: false })
      : profiles;
    await this.nativeBridge?.save(next);
    if (next.useForVisuals && this.nativeBridge) {
      for (const profile of this.profiles) {
        if (priorVisualProfileIds.has(profile.id)) {
          await this.nativeBridge.save(profile);
        }
      }
    }
    await this.persist();
    this.fireChange();
    return summary(next);
  }

  async select(id: string | null): Promise<void> {
    await this.ready();
    if (id && !this.profiles.some((profile) => profile.id === id && profile.enabled)) {
      throw new Error('That personal AI profile is unavailable or disabled.');
    }
    await this.nativeBridge?.select(id);
    await this.context.globalState.update(ACTIVE_PROFILE_KEY, id ?? undefined);
    this.fireChange();
  }

  async setEnabled(id: string, enabled: boolean): Promise<void> {
    await this.ready();
    const profile = this.profiles.find((candidate) => candidate.id === id);
    if (!profile) throw new Error('Personal AI profile not found.');
    await this.nativeBridge?.setEnabled(id, enabled);
    profile.enabled = enabled;
    profile.updatedAt = new Date().toISOString();
    if (!enabled && this.activeId() === id) {
      await this.context.globalState.update(ACTIVE_PROFILE_KEY, undefined);
    }
    await this.persist();
    this.fireChange();
  }

  async delete(id: string): Promise<void> {
    await this.ready();
    const before = this.profiles.length;
    if (this.profiles.some((profile) => profile.id === id)) {
      await this.nativeBridge?.delete(id);
    }
    this.profiles = this.profiles.filter((profile) => profile.id !== id);
    if (this.profiles.length === before) return;
    if (this.context.globalState.get<string>(ACTIVE_PROFILE_KEY) === id) {
      await this.context.globalState.update(ACTIVE_PROFILE_KEY, undefined);
    }
    await this.persist();
    this.fireChange();
  }

  async probe(id: string): Promise<PersonalAiProbeResult> {
    await this.ready();
    const profile = this.get(id);
    if (!profile) throw new Error('Personal AI profile not found.');
    return probePersonalAiProfile(profile);
  }

  dispose(): void {
    this.listeners.clear();
  }

  private async load(): Promise<void> {
    const serialized = await this.context.secrets.get(SECRET_KEY);
    if (!serialized) return;
    try {
      const parsed = JSON.parse(serialized) as unknown;
      if (!Array.isArray(parsed)) return;
      this.profiles = parsed
        .filter(isStoredProfile)
        .map((profile) => ({
          ...profile,
          visionModel: profile.visionModel?.trim() || profile.model,
          useForVisuals: profile.useForVisuals === true,
        }));
      let visualClaimed = false;
      this.profiles = this.profiles.map((profile) => {
        if (!profile.useForVisuals) return profile;
        if (visualClaimed) return { ...profile, useForVisuals: false };
        visualClaimed = true;
        return profile;
      });
    } catch {
      this.profiles = [];
    }
    if (this.nativeBridge) {
      try {
        for (const profile of this.profiles) await this.nativeBridge.save(profile);
        await this.nativeBridge.select(this.activeId());
      } catch {
        // A non-Founder VS Code host may not expose the native bridge. The
        // encrypted extension store remains usable by the chat participant.
      }
    }
    this.fireChange();
  }

  private async persist(): Promise<void> {
    await this.context.secrets.store(SECRET_KEY, JSON.stringify(this.profiles));
  }

  private fireChange(): void {
    for (const listener of this.listeners) listener();
  }
}
