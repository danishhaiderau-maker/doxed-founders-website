/*--------------------------------------------------------------------------------------
 * Founder Personal AI command bridge.
 * Founder Settings owns the public UX; these commands synchronize its encrypted
 * profile records into the native chat model picker and direct-provider runtime.
 *--------------------------------------------------------------------------------------*/

import { Action2, registerAction2 } from '../../../../platform/actions/common/actions.js';
import type { ServicesAccessor } from '../../../../editor/browser/editorExtensions.js';
import { IVoidSettingsService } from '../common/voidSettingsService.js';
import {
	readFounderProviderProfiles,
	writeFounderProviderProfiles,
	type FounderProviderProfile,
} from '../common/founderProviderProfiles.js';

type FounderPersonalAiInput = {
	id?: string;
	name?: string;
	label?: string;
	kind?: 'openai-compatible' | 'ollama';
	baseUrl?: string;
	apiKey?: string;
	model?: string;
	headers?: Record<string, string>;
	enabled?: boolean;
	createdAt?: string;
	updatedAt?: string;
};

const BLOCKED_HEADERS = new Set([
	'authorization',
	'connection',
	'content-length',
	'content-type',
	'host',
	'proxy-authorization',
	'transfer-encoding',
]);

const isPrivateIpv4 = (hostname: string): boolean => {
	const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(hostname);
	if (!match) return false;
	const octets = match.slice(1).map(Number);
	if (octets.some(value => value < 0 || value > 255)) return false;
	return octets[0] === 10
		|| octets[0] === 127
		|| (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
		|| (octets[0] === 192 && octets[1] === 168);
};

const isLocalEndpoint = (hostname: string): boolean => {
	const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, '');
	return normalized === 'localhost'
		|| normalized === '::1'
		|| normalized.endsWith('.local')
		|| isPrivateIpv4(normalized);
};

const cleanBaseUrl = (rawValue: string): string => {
	let parsed: URL;
	try {
		parsed = new URL(rawValue.trim().replace(/\/+$/, ''));
	} catch {
		throw new Error('Personal AI base URL must include https://.');
	}
	if (parsed.username || parsed.password || parsed.search || parsed.hash) {
		throw new Error('Personal AI URLs cannot contain credentials, query parameters, or fragments.');
	}
	if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && isLocalEndpoint(parsed.hostname))) {
		throw new Error('Remote Personal AI providers must use HTTPS. HTTP is allowed only on local or private networks.');
	}
	const suffix = '/chat/completions';
	if (parsed.pathname.toLowerCase().endsWith(suffix)) {
		parsed.pathname = parsed.pathname.slice(0, -suffix.length) || '/';
	}
	return parsed.toString().replace(/\/+$/, '');
};

const cleanHeaders = (input: Record<string, string>): Record<string, string> => {
	const entries = Object.entries(input);
	if (entries.length > 24) throw new Error('Use no more than 24 optional headers.');
	const headers: Record<string, string> = {};
	for (const [rawName, rawValue] of entries) {
		const name = rawName.trim();
		if (!/^[A-Za-z0-9-]{1,80}$/.test(name)) throw new Error(`Invalid header name: ${rawName}`);
		if (BLOCKED_HEADERS.has(name.toLowerCase())) throw new Error(`${name} is managed by Founder and cannot be overridden.`);
		if (typeof rawValue !== 'string' || rawValue.length > 2_000) {
			throw new Error(`${name} must have a string value shorter than 2,000 characters.`);
		}
		headers[name] = rawValue;
	}
	return headers;
};

const cleanInput = (
	input: FounderPersonalAiInput,
	existing?: FounderProviderProfile,
): FounderProviderProfile => {
	const label = (input.name ?? input.label ?? '').trim();
	const model = (input.model ?? '').trim();
	const rawBaseUrl = cleanBaseUrl(input.baseUrl ?? '');
	if (!label || label.length > 60) throw new Error('Personal AI name must be 1-60 characters.');
	if (!model || model.length > 200) throw new Error('Personal AI model ID must be 1-200 characters.');
	if (label.startsWith('founder-os-')) throw new Error('Names beginning with founder-os- are reserved.');
	const headers = cleanHeaders(input.headers ?? existing?.headers ?? {});
	const apiKey = input.apiKey?.trim() || existing?.apiKey || '';
	const kind = input.kind === 'ollama' ? 'ollama' : 'openai-compatible';
	const baseUrl = kind === 'ollama' && !/\/v1$/i.test(rawBaseUrl) ? `${rawBaseUrl}/v1` : rawBaseUrl;
	if (kind === 'openai-compatible' && !apiKey) throw new Error('An API key is required for a remote Personal AI profile.');
	const now = new Date().toISOString();
	return {
		id: input.id?.trim() || existing?.id || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
		label,
		kind,
		baseUrl,
		apiKey,
		model,
		headers,
		enabled: input.enabled ?? existing?.enabled ?? true,
		createdAt: input.createdAt ?? existing?.createdAt ?? now,
		updatedAt: input.updatedAt ?? now,
	};
};

const summary = (profile: FounderProviderProfile, active: boolean) => ({
	id: profile.id,
	name: profile.label,
	kind: profile.kind ?? 'openai-compatible',
	baseUrl: profile.baseUrl,
	model: profile.model,
	enabled: profile.enabled !== false,
	hasApiKey: Boolean(profile.apiKey),
	headerNames: Object.keys(profile.headers).sort(),
	createdAt: profile.createdAt ?? '',
	updatedAt: profile.updatedAt ?? '',
	active,
});

const profilesAndProvider = (service: IVoidSettingsService) => {
	const provider = service.state.settingsOfProvider.openAICompatible;
	return { provider, profiles: readFounderProviderProfiles(provider.headersJSON) };
};

const writeProfiles = async (
	service: IVoidSettingsService,
	profiles: FounderProviderProfile[],
	previousLabel?: string,
) => {
	const state = service.state;
	const provider = state.settingsOfProvider.openAICompatible;
	const profileLabels = new Set(profiles.map(profile => profile.label));
	const preservedModels = provider.models.filter(entry =>
		entry.modelName.startsWith('founder-os-')
		|| (!profileLabels.has(entry.modelName) && entry.modelName !== previousLabel),
	);
	const profileModels = profiles.map(profile => ({
		modelName: profile.label,
		type: 'custom' as const,
		isHidden: profile.enabled === false,
	}));
	await service.dangerousSetState({
		...state,
		settingsOfProvider: {
			...state.settingsOfProvider,
			openAICompatible: {
				...provider,
				headersJSON: writeFounderProviderProfiles(provider.headersJSON, profiles),
				models: [...preservedModels, ...profileModels],
			},
		},
	});
};

registerAction2(class extends Action2 {
	constructor() { super({ id: 'founder.personalAi.list', title: { value: 'Founder: List Personal AI', original: 'Founder: List Personal AI' } }); }
	async run(accessor: ServicesAccessor) {
		const service = accessor.get(IVoidSettingsService);
		await service.waitForInitState;
		const { profiles } = profilesAndProvider(service);
		const active = service.state.modelSelectionOfFeature.Chat?.modelName;
		return profiles.map(profile => summary(profile, profile.label === active));
	}
});

registerAction2(class extends Action2 {
	constructor() { super({ id: 'founder.managedAi.select', title: { value: 'Founder: Select Managed AI', original: 'Founder: Select Managed AI' } }); }
	async run(accessor: ServicesAccessor, modelName: string) {
		const allowed = new Set(['founder-os-auto', 'founder-os-fast', 'founder-os-code', 'founder-os-reasoning']);
		if (!allowed.has(modelName)) throw new Error('Unknown Founder managed route.');
		const service = accessor.get(IVoidSettingsService);
		await service.waitForInitState;
		await service.setModelSelectionOfFeature('Chat', { providerName: 'openAICompatible', modelName });
	}
});

registerAction2(class extends Action2 {
	constructor() { super({ id: 'founder.personalAi.save', title: { value: 'Founder: Save Personal AI', original: 'Founder: Save Personal AI' } }); }
	async run(accessor: ServicesAccessor, input: FounderPersonalAiInput) {
		const service = accessor.get(IVoidSettingsService);
		await service.waitForInitState;
		const { profiles } = profilesAndProvider(service);
		const existing = input.id ? profiles.find(profile => profile.id === input.id) : undefined;
		const next = cleanInput(input, existing);
		if (profiles.some(profile => profile.id !== next.id && profile.label.toLowerCase() === next.label.toLowerCase())) {
			throw new Error('Choose a unique Personal AI name.');
		}
		const updated = existing
			? profiles.map(profile => profile.id === existing.id ? next : profile)
			: [...profiles, next];
		await writeProfiles(service, updated, existing?.label);
		return summary(next, false);
	}
});

registerAction2(class extends Action2 {
	constructor() { super({ id: 'founder.personalAi.select', title: { value: 'Founder: Select Personal AI', original: 'Founder: Select Personal AI' } }); }
	async run(accessor: ServicesAccessor, id: string | null) {
		const service = accessor.get(IVoidSettingsService);
		await service.waitForInitState;
		if (!id) {
			await service.setModelSelectionOfFeature('Chat', { providerName: 'openAICompatible', modelName: 'founder-os-auto' });
			return;
		}
		const { profiles } = profilesAndProvider(service);
		const profile = profiles.find(candidate => candidate.id === id && candidate.enabled !== false);
		if (!profile) throw new Error('Personal AI profile is unavailable or disabled.');
		await service.setModelSelectionOfFeature('Chat', { providerName: 'openAICompatible', modelName: profile.label });
	}
});

registerAction2(class extends Action2 {
	constructor() { super({ id: 'founder.personalAi.enable', title: { value: 'Founder: Enable Personal AI', original: 'Founder: Enable Personal AI' } }); }
	async run(accessor: ServicesAccessor, input: { id: string; enabled: boolean }) {
		const service = accessor.get(IVoidSettingsService);
		await service.waitForInitState;
		const { profiles } = profilesAndProvider(service);
		const profile = profiles.find(candidate => candidate.id === input.id);
		if (!profile) throw new Error('Personal AI profile not found.');
		const updated = profiles.map(candidate => candidate.id === profile.id
			? { ...candidate, enabled: input.enabled, updatedAt: new Date().toISOString() }
			: candidate);
		if (!input.enabled && service.state.modelSelectionOfFeature.Chat?.modelName === profile.label) {
			await service.setModelSelectionOfFeature('Chat', { providerName: 'openAICompatible', modelName: 'founder-os-auto' });
		}
		await writeProfiles(service, updated);
	}
});

registerAction2(class extends Action2 {
	constructor() { super({ id: 'founder.personalAi.delete', title: { value: 'Founder: Delete Personal AI', original: 'Founder: Delete Personal AI' } }); }
	async run(accessor: ServicesAccessor, id: string) {
		const service = accessor.get(IVoidSettingsService);
		await service.waitForInitState;
		const { profiles } = profilesAndProvider(service);
		const profile = profiles.find(candidate => candidate.id === id);
		if (!profile) return;
		if (service.state.modelSelectionOfFeature.Chat?.modelName === profile.label) {
			await service.setModelSelectionOfFeature('Chat', { providerName: 'openAICompatible', modelName: 'founder-os-auto' });
		}
		await writeProfiles(service, profiles.filter(candidate => candidate.id !== id), profile.label);
	}
});
