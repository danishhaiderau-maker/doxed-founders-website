import { proxyAwareNativeRequest } from './proxy-aware-request';

const MAX_VISUAL_ATTACHMENTS = 4;
const MAX_VISUAL_BASE64_CHARS = 5_600_000;
const MAX_VISUAL_REQUEST_BASE64_CHARS = 9_300_000;
const VISUAL_TIMEOUT_MS = 60_000;
const MAX_VISUAL_RESPONSE_BYTES = 2_000_000;
const VISUAL_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

export type ManagedVisualAttachment = {
  name?: string;
  mimeType?: string;
  dataBase64?: string;
};

export type ManagedVisualInput = {
  attachments?: ManagedVisualAttachment[];
};

export type ManagedVisualResult = {
  descriptions: Array<{
    name: string;
    description: string;
  }>;
  provider: 'glm';
  model: 'glm-4.6v-flash';
  route: 'founder-managed-vision';
};

export type ManagedVisualCredentials = {
  apiBaseUrl: string;
  authorization: string;
};

export type ManagedVisualRequestDeps = {
  fetchImpl?: typeof fetch;
  nativeFetchImpl?: (
    url: string,
    body: Buffer,
    authorization: string,
  ) => Promise<Response>;
  platform?: NodeJS.Platform;
};

function normalizeAttachments(
  input: ManagedVisualInput,
): Array<{ name: string; mimeType: string; dataBase64: string }> {
  if (
    !Array.isArray(input?.attachments) ||
    input.attachments.length < 1 ||
    input.attachments.length > MAX_VISUAL_ATTACHMENTS
  ) {
    throw new Error('Attach between one and four screenshots.');
  }
  let totalChars = 0;
  return input.attachments.map((attachment, index) => {
    const name = attachment.name?.trim().slice(0, 180) || `screenshot-${index + 1}`;
    const mimeType = attachment.mimeType?.trim().toLowerCase() ?? '';
    const dataBase64 = attachment.dataBase64?.trim() ?? '';
    if (!VISUAL_TYPES.has(mimeType)) {
      throw new Error('Founder accepts PNG, JPEG, and WebP screenshots.');
    }
    if (
      !dataBase64 ||
      dataBase64.length > MAX_VISUAL_BASE64_CHARS ||
      !/^[a-zA-Z0-9+/]+={0,2}$/.test(dataBase64)
    ) {
      throw new Error(`${name} is invalid or larger than 4 MB.`);
    }
    totalChars += dataBase64.length;
    if (totalChars > MAX_VISUAL_REQUEST_BASE64_CHARS) {
      throw new Error('Attached screenshots exceed the 7 MB total limit.');
    }
    return { name, mimeType, dataBase64 };
  });
}

async function nativeVisualFetch(
  url: string,
  body: Buffer,
  authorization: string,
): Promise<Response> {
  return proxyAwareNativeRequest({
    url,
    method: 'POST',
    headers: {
      Authorization: authorization,
      'Content-Type': 'application/json',
    },
    body,
    timeoutMs: VISUAL_TIMEOUT_MS,
    maxResponseBytes: MAX_VISUAL_RESPONSE_BYTES,
  });
}

function visualError(status: number): string {
  if (status === 401 || status === 403) {
    return 'Reconnect Founder IDE before attaching screenshots.';
  }
  if (status === 413) return 'The attached screenshots are too large.';
  if (status === 415) return 'Founder accepts PNG, JPEG, and WebP screenshots.';
  if (status === 429) {
    return 'Founder screenshot reading is busy. Wait a moment and try again.';
  }
  if (status === 503) {
    return 'Founder screenshot reading is not configured yet. Ask an admin to enable Founder Vision.';
  }
  return `Founder could not read the attached screenshots (${status}).`;
}

export async function describeManagedVisualRequest(
  input: ManagedVisualInput,
  credentials: ManagedVisualCredentials,
  deps: ManagedVisualRequestDeps = {},
): Promise<ManagedVisualResult> {
  const attachments = normalizeAttachments(input);
  const body = Buffer.from(JSON.stringify({ attachments }), 'utf8');
  const url =
    `${credentials.apiBaseUrl.replace(/\/$/, '')}/api/v1/images/descriptions`;

  let response: Response;
  try {
    response = (deps.platform ?? process.platform) === 'win32'
      ? await (deps.nativeFetchImpl ?? nativeVisualFetch)(
        url,
        body,
        credentials.authorization,
      )
      : await (deps.fetchImpl ?? fetch)(url, {
        method: 'POST',
        headers: {
          Authorization: credentials.authorization,
          'Content-Type': 'application/json',
        },
        body,
        signal: AbortSignal.timeout(VISUAL_TIMEOUT_MS),
      });
  } catch {
    throw new Error(
      'Founder could not reach screenshot reading. Your attachments are still in the composer.',
    );
  }

  if (!response.ok) throw new Error(visualError(response.status));

  const payload = (await response.json()) as Partial<ManagedVisualResult>;
  if (
    !Array.isArray(payload.descriptions) ||
    payload.descriptions.length !== attachments.length
  ) {
    throw new Error(
      'Founder screenshot reading returned an invalid response. Your attachments are still in the composer.',
    );
  }
  const descriptions = payload.descriptions.map((item, index) => ({
    name:
      typeof item?.name === 'string' && item.name.trim()
        ? item.name.trim().slice(0, 180)
        : attachments[index].name,
    description:
      typeof item?.description === 'string' ? item.description.trim() : '',
  }));
  if (descriptions.some((item) => !item.description)) {
    throw new Error(
      'Founder could not understand every attached screenshot. Remove the unclear image or try again.',
    );
  }
  return {
    descriptions,
    provider: 'glm',
    model: 'glm-4.6v-flash',
    route: 'founder-managed-vision',
  };
}
