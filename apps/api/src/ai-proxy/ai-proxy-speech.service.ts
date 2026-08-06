import {
  BadGatewayException,
  Injectable,
  ServiceUnavailableException,
} from '@nestjs/common';
import { FounderPromoService } from '../founder-os/founder-promo.service';

/**
 * MANAGED SPEECH (voice transcription) for the Founder IDE.
 *
 * COST EXCEPTION (read me): GLM is reserved EXCLUSIVELY for the Second Brain
 * surface across the codebase. Speech (ASR) is the ONE deliberate exception,
 * because no other configured provider (DeepSeek is text-only; Gemini has no
 * ASR) offers speech-to-text. To prevent silent GLM spend on general traffic,
 * managed speech is OFF BY DEFAULT and only activates when an operator
 * explicitly sets `SPEECH_PROVIDER=glm` in env. Second Brain remains the only
 * other sanctioned GLM caller; this endpoint is the documented, opt-in
 * exception. Revisit when a non-GLM ASR provider is wired into the stack.
 */
const GLM_SPEECH_ENDPOINT =
  'https://api.z.ai/api/paas/v4/audio/transcriptions';
const GLM_SPEECH_MODEL = 'glm-asr-2512';
const SPEECH_TIMEOUT_MS = 45_000;

/** Must be explicitly enabled by an operator before any GLM token is spent. */
function speechEnabled(): boolean {
  return process.env.SPEECH_PROVIDER?.trim().toLowerCase() === 'glm';
}

export type FounderSpeechResult = {
  text: string;
  provider: 'glm';
  model: typeof GLM_SPEECH_MODEL;
  route: 'founder-managed-speech';
};

@Injectable()
export class AiProxySpeechService {
  constructor(private readonly founderPromo: FounderPromoService) {}

  async transcribeWav(audio: Uint8Array): Promise<FounderSpeechResult> {
    // Hard gate: refuse unless an operator explicitly opted into GLM speech.
    // This keeps GLM spend deliberate and visible. Default is OFF.
    if (!speechEnabled()) {
      throw new ServiceUnavailableException(
        'Founder managed voice is not enabled. Set SPEECH_PROVIDER=glm to opt into the GLM ASR exception (GLM is otherwise Second-Brain-only).',
      );
    }

    const apiKey =
      await this.founderPromo.getDecryptedPlatformGlmSpeechKey();
    if (!apiKey) {
      throw new ServiceUnavailableException(
        'Founder managed voice is not configured.',
      );
    }

    const form = new FormData();
    form.append('model', GLM_SPEECH_MODEL);
    form.append('stream', 'false');
    form.append(
      'file',
      new Blob([audio], { type: 'audio/wav' }),
      'founder-voice.wav',
    );

    let response: Response;
    try {
      response = await fetch(GLM_SPEECH_ENDPOINT, {
        method: 'POST',
        headers: { Authorization: `Bearer ${apiKey}` },
        body: form,
        signal: AbortSignal.timeout(SPEECH_TIMEOUT_MS),
      });
    } catch (error) {
      throw new BadGatewayException(
        error instanceof DOMException && error.name === 'TimeoutError'
          ? 'Founder managed voice timed out.'
          : 'Founder managed voice could not reach its speech service.',
      );
    }

    if (!response.ok) {
      throw new BadGatewayException(
        `Founder managed voice returned ${response.status}.`,
      );
    }

    const payload = (await response.json()) as { text?: unknown };
    const text = typeof payload.text === 'string' ? payload.text.trim() : '';
    if (!text) {
      throw new BadGatewayException(
        'Founder managed voice did not detect speech.',
      );
    }

    return {
      text,
      provider: 'glm',
      model: GLM_SPEECH_MODEL,
      route: 'founder-managed-speech',
    };
  }
}
