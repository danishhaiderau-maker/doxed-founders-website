/*--------------------------------------------------------------------------------------
 *  Founder IDE encrypted personal provider profiles.
 *  Stored inside Void's encrypted settings payload under a namespaced key.
 *--------------------------------------------------------------------------------------*/

export const FOUNDER_PROVIDER_PROFILES_KEY = '__founderProviderProfilesV1';

export type FounderProviderProfile = {
	id: string;
	label: string;
	kind?: 'openai-compatible' | 'ollama';
	baseUrl: string;
	apiKey: string;
	model: string;
	headers: Record<string, string>;
	enabled?: boolean;
	createdAt?: string;
	updatedAt?: string;
};

type JsonRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is JsonRecord =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

const readRoot = (raw: string | undefined): JsonRecord => {
	if (!raw?.trim()) return {};
	try {
		const parsed: unknown = JSON.parse(raw);
		return isRecord(parsed) ? parsed : {};
	} catch {
		return {};
	}
};

const cleanHeaders = (value: unknown): Record<string, string> => {
	if (!isRecord(value)) return {};
	return Object.fromEntries(
		Object.entries(value)
			.filter((entry): entry is [string, string] =>
				Boolean(entry[0].trim()) && typeof entry[1] === 'string')
			.map(([key, headerValue]) => [key.trim(), headerValue]),
	);
};

const cleanProfile = (value: unknown): FounderProviderProfile | null => {
	if (!isRecord(value)) return null;
	const id = typeof value.id === 'string' ? value.id.trim() : '';
	const label = typeof value.label === 'string' ? value.label.trim() : '';
	const baseUrl = typeof value.baseUrl === 'string'
		? value.baseUrl.trim().replace(/\/+$/, '')
		: '';
	const apiKey = typeof value.apiKey === 'string' ? value.apiKey.trim() : '';
	const model = typeof value.model === 'string' ? value.model.trim() : '';
	if (!id || !label || !baseUrl || !model) return null;
	if (!/^https?:\/\//i.test(baseUrl)) return null;
	if (label.startsWith('founder-os-')) return null;
	const kind = value.kind === 'ollama' ? 'ollama' : 'openai-compatible';
	const enabled = value.enabled !== false;
	const createdAt = typeof value.createdAt === 'string' ? value.createdAt : undefined;
	const updatedAt = typeof value.updatedAt === 'string' ? value.updatedAt : undefined;
	return { id, label, kind, baseUrl, apiKey, model, headers: cleanHeaders(value.headers), enabled, createdAt, updatedAt };
};

export const readFounderProviderProfiles = (
	rawHeadersJson: string | undefined,
): FounderProviderProfile[] => {
	const root = readRoot(rawHeadersJson);
	const stored = root[FOUNDER_PROVIDER_PROFILES_KEY];
	if (!Array.isArray(stored)) return [];
	const seenIds = new Set<string>();
	const seenLabels = new Set<string>();
	const profiles: FounderProviderProfile[] = [];
	for (const value of stored) {
		const profile = cleanProfile(value);
		if (!profile || seenIds.has(profile.id) || seenLabels.has(profile.label.toLowerCase())) continue;
		seenIds.add(profile.id);
		seenLabels.add(profile.label.toLowerCase());
		profiles.push(profile);
	}
	return profiles;
};

export const writeFounderProviderProfiles = (
	rawHeadersJson: string | undefined,
	profiles: FounderProviderProfile[],
): string => {
	const root = readRoot(rawHeadersJson);
	root[FOUNDER_PROVIDER_PROFILES_KEY] = profiles
		.map(cleanProfile)
		.filter((profile): profile is FounderProviderProfile => Boolean(profile));
	return JSON.stringify(root);
};

export const headersWithoutFounderProviderProfiles = (
	rawHeadersJson: string | undefined,
): string => {
	const root = readRoot(rawHeadersJson);
	delete root[FOUNDER_PROVIDER_PROFILES_KEY];
	return JSON.stringify(root);
};

export const resolveFounderProviderProfile = (
	rawHeadersJson: string | undefined,
	selectionName: string,
): FounderProviderProfile | null =>
	readFounderProviderProfiles(rawHeadersJson)
		.find((profile) => profile.enabled !== false && profile.label === selectionName) ?? null;
