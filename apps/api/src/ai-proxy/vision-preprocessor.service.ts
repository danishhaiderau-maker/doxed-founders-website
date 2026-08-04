import { Injectable, Logger } from '@nestjs/common';
import { getVisionApiBaseUrl, getVisionModel } from '../founder-os/glm-config';
import { FounderBrainProvidersService } from '../founder-ai-runtime/founder-brain-providers.service';
import type { ChatCompletionMessageContentPart } from './dto/ai-proxy.dto';

/**
 * GLM-4V vision preprocessor.
 *
 * Triggered by AiProxyRuntimeService.invoke() when an inbound chat request
 * contains image attachments AND the resolved route's model has no vision
 * capability (see Capability.vision). The preprocessor sends each image to
 * GLM-4V via the z.ai general endpoint, returns a short text description,
 * and the runtime substitutes the image part with that text before invoking
 * the vision-blind coding model.
 *
 * Conservative by design — matches the existing GLM-5.2 wiring pattern:
 *   - Reuses FounderBrainProvidersService.resolveApiKey('glm') so the same
 *     credential pipeline (env → promo → routing) is honored.
 *   - Best-effort: any failure (network, non-2xx, malformed JSON) returns a
 *     placeholder string and logs at warn. The request never fails because
 *     vision preprocessing failed — the coding model just receives a less
 *     specific prompt.
 *
 * See docs/PRODUCTION-AI-KEYS.md §3 for env var reference.
 */
@Injectable()
export class VisionPreprocessorService {
  private readonly logger = new Logger(VisionPreprocessorService.name);

  /** Hard cap on the GLM-4V call — preprocessing must never stall the chat turn. */
  private readonly timeoutMs = 12_000;

  constructor(private readonly brainProviders: FounderBrainProvidersService) {}

  /**
   * Returns true iff the message content array contains at least one
   * `image_url` part. Cheap structural check — used to decide whether to
   * resolve the route's Capability row at all.
   */
  hasImageContent(parts: ChatCompletionMessageContentPart[] | undefined | null): boolean {
    if (!parts?.length) return false;
    return parts.some((p) => p?.type === 'image_url' && Boolean(p.image_url?.url));
  }

  /**
   * Send a single image to GLM-4V and return a short text description.
   * Best-effort — never throws.
   */
  async describeImage(
    imageUrl: string,
    hint?: string,
  ): Promise<string> {
    const url = `${getVisionApiBaseUrl()}/chat/completions`;
    const apiKey = await this.brainProviders.resolveApiKey('glm');
    if (!apiKey) {
      this.logger.warn('vision_preprocessor skipped — no GLM API key configured');
      return '[image: vision preprocessor unavailable — no GLM key]';
    }

    const model = this.visionModel();
    const prompt =
      hint && hint.trim().length > 0
        ? `Describe this image concisely for a coding assistant. Context: ${hint.trim().slice(0, 400)}.`
        : 'Describe this image concisely for a coding assistant. Focus on UI, code, errors, or data visible.';

    const payload = {
      model,
      messages: [
        {
          role: 'user',
          content: [
            { type: 'text', text: prompt },
            { type: 'image_url', image_url: { url: imageUrl } },
          ],
        },
      ],
      max_tokens: 400,
      temperature: 0,
      stream: false,
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    timer.unref?.();
    const started = Date.now();

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
        // @ts-expect-error -- node-fetch vs dom fetch typing on signal union
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.text().catch(() => '');
        this.logger.warn(
          `vision_preprocessor http ${res.status}: ${body.slice(0, 200)}`,
        );
        return `[image: vision preprocessor failed (${res.status})]`;
      }

      const data = (await res.json()) as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const text = data?.choices?.[0]?.message?.content?.trim();
      if (!text) {
        this.logger.warn('vision_preprocessor returned empty content');
        return '[image: vision preprocessor returned no description]';
      }

      this.logger.debug(
        `vision_preprocessor.describeImage ok model=${model} latency=${Date.now() - started}ms`,
      );
      return text;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger.warn(`vision_preprocessor error: ${message}`);
      return `[image: vision preprocessor error — ${message.slice(0, 120)}]`;
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Replace image parts in a content array with their GLM-4V text descriptions.
   * Text parts are preserved verbatim. Returns a plain string if every part
   * was an image (the descriptions get concatenated), or the original array
   * with image parts rewritten, depending on shape.
   *
   * The audit hint passed to describeImage is the union of any text parts in
   * the same message — this lets GLM-4V use the user's surrounding prompt as
   * context when describing the image.
   */
  async rewriteContentWithDescriptions(
    parts: ChatCompletionMessageContentPart[],
  ): Promise<string> {
    const textHint = parts
      .filter((p) => p.type === 'text' && typeof p.text === 'string')
      .map((p) => p.text)
      .join(' ')
      .slice(0, 800);

    const descriptions = await Promise.all(
      parts
        .filter((p) => p.type === 'image_url' && Boolean(p.image_url?.url))
        .map((p) => this.describeImage(p.image_url.url, textHint)),
    );

    // Collapse to a single string. The coding model gets the user's text
    // followed by [Image N: description] blocks for each image.
    const textParts = parts
      .filter((p) => p.type === 'text' && typeof p.text === 'string')
      .map((p) => p.text);
    const imageBlocks = descriptions.map(
      (d, i) => `[Image ${i + 1}: ${d}]`,
    );
    return [...textParts, ...imageBlocks].join('\n\n').trim();
  }

  /** Resolve the GLM-4V model name from env (FOUNDER_VISION_MODEL) or default. */
  visionModel(): string {
    return getVisionModel();
  }
}
