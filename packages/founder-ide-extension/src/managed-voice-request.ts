import { proxyAwareNativeRequest } from './proxy-aware-request';

const MAX_TRANSCRIPTION_BASE64_CHARS = 34_000_000;
const MANAGED_VOICE_TIMEOUT_MS = 50_000;
const MAX_CURL_OUTPUT_BYTES = 2_000_000;

export type ManagedVoiceInput = {
  audioBase64?: string;
};

export type ManagedVoiceResult = {
  text: string;
  provider: string;
  model: string;
  route: string;
};

export type ManagedVoiceCredentials = {
  apiBaseUrl: string;
  authorization: string;
};

export type ManagedVoiceRequestDeps = {
  fetchImpl?: typeof fetch;
  nativeFetchImpl?: (
    url: string,
    audio: Buffer,
    authorization: string,
  ) => Promise<Response>;
  platform?: NodeJS.Platform;
};

async function proxyAwareNativeFetch(
  url: string,
  audio: Buffer,
  authorization: string,
): Promise<Response> {
  return proxyAwareNativeRequest({
    url,
    method: 'POST',
    headers: {
      Authorization: authorization,
      'Content-Type': 'audio/wav',
    },
    body: audio,
    timeoutMs: MANAGED_VOICE_TIMEOUT_MS,
    maxResponseBytes: MAX_CURL_OUTPUT_BYTES,
  });
}

export async function transcribeManagedVoiceRequest(
  input: ManagedVoiceInput,
  credentials: ManagedVoiceCredentials,
  deps: ManagedVoiceRequestDeps = {},
): Promise<ManagedVoiceResult> {
  const audioBase64 = input?.audioBase64?.trim();
  if (!audioBase64) {
    throw new Error('No voice recording was provided.');
  }
  if (audioBase64.length > MAX_TRANSCRIPTION_BASE64_CHARS) {
    throw new Error('Voice recording exceeds the 25 MB transcription limit.');
  }

  const audio = Buffer.from(audioBase64, 'base64');
  if (audio.byteLength < 44) {
    throw new Error('The voice recording is empty.');
  }

  let response: Response;
  try {
    const url =
      `${credentials.apiBaseUrl.replace(/\/$/, '')}/api/v1/audio/transcriptions`;
    if ((deps.platform ?? process.platform) === 'win32') {
      response = await (deps.nativeFetchImpl ?? proxyAwareNativeFetch)(
        url,
        audio,
        credentials.authorization,
      );
    } else {
      response = await (deps.fetchImpl ?? fetch)(url, {
        method: 'POST',
        headers: {
          Authorization: credentials.authorization,
          'Content-Type': 'audio/wav',
        },
        body: audio,
        signal: AbortSignal.timeout(MANAGED_VOICE_TIMEOUT_MS),
      });
    }
  } catch (error) {
    throw new Error(
      error instanceof DOMException &&
        (error.name === 'AbortError' || error.name === 'TimeoutError')
        ? 'Founder managed voice timed out. Your typed text is unchanged.'
        : 'Founder managed voice could not reach its speech service. Your typed text is unchanged.',
    );
  }

  if (!response.ok) {
    throw new Error(
      response.status === 401 || response.status === 403
        ? 'Your Founder session needs to be renewed before using voice input.'
        : `Founder managed voice returned ${response.status}. Your typed text is unchanged.`,
    );
  }

  const payload = (await response.json()) as Partial<ManagedVoiceResult>;
  const text = typeof payload.text === 'string' ? payload.text.trim() : '';
  if (!text) {
    throw new Error('No speech was detected in the recording.');
  }

  return {
    text,
    provider: String(payload.provider ?? 'glm'),
    model: String(payload.model ?? 'glm-asr-2512'),
    route: String(payload.route ?? 'founder-managed-speech'),
  };
}
