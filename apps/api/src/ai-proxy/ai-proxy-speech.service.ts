import {
  BadGatewayException,
  Injectable,
  ServiceUnavailableException,
} from '@nestjs/common';
import { FounderPromoService } from '../founder-os/founder-promo.service';
import { ProviderEgressAuditService } from '../founder-ai-runtime/provider-egress-audit.service';

const GLM_SPEECH_ENDPOINT =
  'https://api.z.ai/api/paas/v4/audio/transcriptions';
const GLM_SPEECH_MODEL = 'glm-asr-2512';
const SPEECH_TIMEOUT_MS = 45_000;

export type FounderSpeechResult = {
  text: string;
  provider: 'glm';
  model: typeof GLM_SPEECH_MODEL;
  route: 'founder-managed-speech';
};

@Injectable()
export class AiProxySpeechService {
  constructor(
    private readonly founderPromo: FounderPromoService,
    private readonly providerEgressAudit: ProviderEgressAuditService,
  ) {}

  async transcribeWav(audio: Uint8Array): Promise<FounderSpeechResult> {
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
      response = await this.providerEgressAudit.runWithContext(
        {
          boundary: 'managed_auxiliary',
          callSiteId: 'ai_proxy.speech',
          budgetDomain: 'founder_managed_speech',
        },
        async () => {
          this.providerEgressAudit.record({
            adapterName: 'ai-proxy.glm-speech',
            provider: 'glm',
          });
          return fetch(GLM_SPEECH_ENDPOINT, {
            method: 'POST',
            headers: { Authorization: `Bearer ${apiKey}` },
            body: form,
            signal: AbortSignal.timeout(SPEECH_TIMEOUT_MS),
          });
        },
      );
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
