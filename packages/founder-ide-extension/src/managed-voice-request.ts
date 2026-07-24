import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const MAX_TRANSCRIPTION_BASE64_CHARS = 34_000_000;
const MANAGED_VOICE_TIMEOUT_MS = 50_000;
const MAX_CURL_OUTPUT_BYTES = 2_000_000;
const CURL_STATUS_MARKER = '__FOUNDER_STATUS__:';

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

function curlConfigValue(value: string): string {
  return value
    .replaceAll('\\', '\\\\')
    .replaceAll('"', '\\"')
    .replaceAll('\r', '')
    .replaceAll('\n', '');
}

async function proxyAwareNativeFetch(
  url: string,
  audio: Buffer,
  authorization: string,
): Promise<Response> {
  const tempDir = await mkdtemp(path.join(os.tmpdir(), 'founder-voice-'));
  const audioPath = path.join(tempDir, 'voice.wav');
  const executable = process.platform === 'win32' ? 'curl.exe' : 'curl';

  try {
    await writeFile(audioPath, audio);
    const config = [
      `url = "${curlConfigValue(url)}"`,
      'request = "POST"',
      `header = "Authorization: ${curlConfigValue(authorization)}"`,
      'header = "Content-Type: audio/wav"',
      `data-binary = "@${curlConfigValue(audioPath)}"`,
      'silent',
      'show-error',
      `max-time = ${Math.ceil(MANAGED_VOICE_TIMEOUT_MS / 1_000)}`,
      'connect-timeout = 15',
      `write-out = "${curlConfigValue(`${CURL_STATUS_MARKER}%{http_code}`)}"`,
    ].join('\n');

    const output = await new Promise<string>((resolve, reject) => {
      const child = spawn(executable, ['--config', '-'], {
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      const stdout: Buffer[] = [];
      let outputBytes = 0;
      let settled = false;
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback();
      };
      const timer = setTimeout(() => {
        child.kill();
        finish(() => reject(new Error('Founder native voice transport timed out.')));
      }, MANAGED_VOICE_TIMEOUT_MS + 5_000);

      child.stdout.on('data', (chunk: Buffer) => {
        outputBytes += chunk.byteLength;
        if (outputBytes > MAX_CURL_OUTPUT_BYTES) {
          child.kill();
          finish(() => reject(new Error('Founder native voice response was too large.')));
          return;
        }
        stdout.push(chunk);
      });
      child.stderr.resume();
      child.on('error', () => {
        finish(() => reject(new Error('Founder native voice transport is unavailable.')));
      });
      child.on('close', (code) => {
        finish(() => {
          if (code !== 0) {
            reject(new Error('Founder native voice transport could not reach its speech service.'));
            return;
          }
          resolve(Buffer.concat(stdout).toString('utf8'));
        });
      });
      child.stdin.end(config);
    });

    const marker = output.lastIndexOf(CURL_STATUS_MARKER);
    if (marker < 0) {
      throw new Error('Founder native voice transport returned an invalid response.');
    }
    const status = Number.parseInt(
      output.slice(marker + CURL_STATUS_MARKER.length).trim(),
      10,
    );
    if (!Number.isInteger(status) || status < 100 || status > 599) {
      throw new Error('Founder native voice transport returned an invalid status.');
    }
    return new Response(output.slice(0, marker), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  } finally {
    await rm(tempDir, { recursive: true, force: true }).catch(() => undefined);
  }
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
