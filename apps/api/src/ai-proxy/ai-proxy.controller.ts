import {
  Controller,
  Get,
  Post,
  Body,
  Query,
  Req,
  Res,
  HttpException,
  HttpStatus,
  UseGuards,
} from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import {
  FounderNodeGuard,
  type FounderNodeRequestUser,
} from '../founder-node/founder-node.guard';
import {
  AiProxyRuntimeService,
  type ProxyAuth,
} from './ai-proxy-runtime.service';
import { AiProxyUsageService } from './ai-proxy-usage.service';
import {
  ChatCompletionRequestDto,
  FimCompletionRequestDto,
} from './dto/ai-proxy.dto';
import type { Response, Request } from 'express';
import { pipeAiProxySseResponse } from './ai-proxy-response-stream';
import { AiProxySpeechService } from './ai-proxy-speech.service';

const MAX_FOUNDER_SPEECH_BYTES = 25 * 1024 * 1024;

type AuthedRequest = Request & {
  user?: { id?: string; sub?: string; userId?: string };
};

/**
 * OpenAI-compatible AI Proxy.
 *
 * Routes are mounted under /v1 so any client expecting the OpenAI schema
 * (Cursor, continue.dev, litellm, raw `openai` SDK) can point at
 *   {API_URL}/v1  with  Authorization: FounderNode {nodeId}:{nodeToken}
 *
 * Auth is via Founder Node tokens (validated by FounderNodeGuard). We mark
 * the whole controller @Public() so the global JwtAuthGuard short-circuits —
 * the route-level FounderNodeGuard then enforces node credentials.
 */
@Public()
@Controller('v1')
export class AiProxyController {
  constructor(
    private readonly runtimeService: AiProxyRuntimeService,
    private readonly usageService: AiProxyUsageService,
    private readonly speechService: AiProxySpeechService,
  ) {}

  /** OpenAI-compatible /v1/models — expands our aliases to a model list. */
  @UseGuards(FounderNodeGuard)
  @Get('models')
  models() {
    return this.runtimeService.listModels();
  }

  /** Aggregated usage for the /settings/ai-usage dashboard (Founder Node auth). */
  @UseGuards(FounderNodeGuard)
  @Get('usage')
  async usage(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Query('days') days?: string,
  ) {
    const parsedDays = Math.max(1, Math.min(365, Number(days ?? '30') || 30));
    const to = new Date();
    const from = new Date(to.getTime() - parsedDays * 24 * 60 * 60 * 1000);
    return this.usageService.summarize(req.founderNode.userId, { from, to });
  }

  /**
   * JWT-guarded usage endpoint for the web dashboard.
   *
   * The web dashboard at /settings/ai-usage sends `Authorization: Bearer <jwt>`
   * (NextAuth session token), not a Founder Node token. The FounderNode-guarded
   * /v1/usage above is for the Founder Node itself. This route accepts either
   * an anonymous request (returns 401) or a JWT-authenticated request.
   *
   * Marked @Public() at controller level + route-level OptionalJwtAuthGuard so
   * we can resolve the user from the JWT if present, otherwise 401.
   */
  @UseGuards(OptionalJwtAuthGuard)
  @Get('usage-for-me')
  async usageForMe(@Req() req: AuthedRequest, @Query('days') days?: string) {
    const userId = req.user?.id ?? req.user?.sub ?? req.user?.userId;
    if (!userId) {
      throw new HttpException(
        { error: { message: 'Authentication required' } },
        HttpStatus.UNAUTHORIZED,
      );
    }
    const parsedDays = Math.max(1, Math.min(365, Number(days ?? '30') || 30));
    const to = new Date();
    const from = new Date(to.getTime() - parsedDays * 24 * 60 * 60 * 1000);
    return this.usageService.summarize(userId, { from, to });
  }

  /** OpenAI-compatible /v1/chat/completions — routes through the proxy. */
  @UseGuards(FounderNodeGuard)
  @Post('chat/completions')
  async chatCompletions(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: ChatCompletionRequestDto,
    @Res() res: Response,
  ): Promise<void> {
    const auth: ProxyAuth = {
      userId: req.founderNode.userId,
      nodeId: req.founderNode.nodeId,
    };
    return this.streamChat(res, auth, body);
  }

  /**
   * Authenticated speech-to-text for Founder IDE. The desktop sends WAV bytes
   * through its Founder Node identity; the platform speech credential never
   * leaves the API process. The resulting text stays in the composer and is
   * submitted through the user's selected route (Founder Auto by default).
   */
  @UseGuards(FounderNodeGuard)
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  @Post('audio/transcriptions')
  async transcribeVoice(
    @Req() req: Request & { founderNode: FounderNodeRequestUser },
    @Res() res: Response,
  ): Promise<void> {
    const contentType = String(req.headers['content-type'] ?? '')
      .split(';', 1)[0]
      .trim()
      .toLowerCase();
    if (contentType !== 'audio/wav' && contentType !== 'audio/x-wav') {
      throw new HttpException(
        { error: { message: 'Founder voice accepts WAV audio only.' } },
        HttpStatus.UNSUPPORTED_MEDIA_TYPE,
      );
    }

    const chunks: Buffer[] = [];
    let total = 0;
    for await (const raw of req as unknown as AsyncIterable<
      Uint8Array | string
    >) {
      const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
      total += chunk.byteLength;
      if (total > MAX_FOUNDER_SPEECH_BYTES) {
        throw new HttpException(
          { error: { message: 'Founder voice recording exceeds 25 MB.' } },
          HttpStatus.PAYLOAD_TOO_LARGE,
        );
      }
      chunks.push(chunk);
    }
    if (total < 44) {
      throw new HttpException(
        { error: { message: 'Founder voice recording is empty.' } },
        HttpStatus.BAD_REQUEST,
      );
    }

    const result = await this.speechService.transcribeWav(
      Buffer.concat(chunks, total),
    );
    res.status(HttpStatus.OK).json(result);
  }

  /**
   * JWT-authenticated chat completions for the Founder OS Phone Remote UI.
   *
   * The desktop IDE path (`/v1/chat/completions` above) authenticates with a
   * Founder Node bearer (`fos_{nodeId}:{nodeToken}`) via FounderNodeGuard.
   * The phone browser cannot present a node token — it only has the founder's
   * NextAuth session JWT. This endpoint mirrors the desktop flow but resolves
   * the user from the JWT via OptionalJwtAuthGuard, then runs the exact same
   * decideRoute → invoke → SSE metadata pipeline using the user's id as the
   * auth context (with a synthetic `nodeId: 'phone'` so usage logs are
   * attributable). See docs/FOUNDER-IDE-FORK-PLAN.md §8 and the Phone Remote
   * design (apps/web/src/app/phone).
   */
  @UseGuards(OptionalJwtAuthGuard)
  @Post('chat/phone-completions')
  async chatPhoneCompletions(
    @Req() req: AuthedRequest,
    @Body() body: ChatCompletionRequestDto,
    @Res() res: Response,
  ): Promise<void> {
    const userId = req.user?.id ?? req.user?.sub ?? req.user?.userId;
    if (!userId) {
      throw new HttpException(
        { error: { message: 'Authentication required' } },
        HttpStatus.UNAUTHORIZED,
      );
    }
    const auth: ProxyAuth = { userId, nodeId: 'phone' };
    // The phone UI always wants the founderOs metadata line (tier / model /
    // DDollar cost) so it can render route transparency per turn.
    const bodyWithMeta: ChatCompletionRequestDto = {
      ...body,
      founder_os_metadata: body.founder_os_metadata ?? true,
    };
    return this.streamChat(res, auth, bodyWithMeta);
  }

  /**
   * Fill-In-the-Middle (FIM) endpoint for code autocomplete.
   *
   * Accepts prefix + suffix and routes to the founder-os-fast alias
   * with FIM context. Streams completion tokens back as SSE.
   *
   * POST /v1/fim/completions
   * Body: { prefix: string, suffix: string, stop: string[], max_tokens?: number }
   */
  @UseGuards(FounderNodeGuard)
  @Post('fim/completions')
  async fimCompletions(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: FimCompletionRequestDto,
    @Res() res: Response,
  ): Promise<void> {
    const auth: ProxyAuth = {
      userId: req.founderNode.userId,
      nodeId: req.founderNode.nodeId,
    };

    // Wrap FIM body as a ChatCompletionRequestDto with FIM context embedded
    const chatBody: ChatCompletionRequestDto = {
      model: body.model ?? 'founder-os-fast',
      messages: [
        {
          role: 'user',
          content: `<fim_prefix>${body.prefix}<fim_suffix>${body.suffix}<fim_middle>`,
        },
      ],
      stream: true,
      max_tokens: body.max_tokens ?? 256,
      temperature: body.temperature ?? 0,
      stop: body.stop ?? ['<|fim▁end|>', '<fim_prefix>', '<fim_suffix>'],
      fim: { prefix: body.prefix, suffix: body.suffix, stop: body.stop },
    };

    return this.streamChat(res, auth, chatBody);
  }

  /**
   * Shared decideRoute → invoke → SSE pipeline used by both the Founder Node
   * (desktop IDE) and JWT (phone remote) chat endpoints. Emits the optional
   * `founderOs` metadata pre-line before piping the upstream stream through.
   */
  private async streamChat(
    res: Response,
    auth: ProxyAuth,
    body: ChatCompletionRequestDto,
  ): Promise<void> {
    const route = await this.runtimeService.decideRoute(auth, body);
    const result = await this.runtimeService.invoke(auth, body, route);

    if (!result.ok) {
      throw new HttpException(
        typeof result.body === 'string'
          ? (() => {
              try {
                return JSON.parse(result.body);
              } catch {
                return { error: { message: result.body } };
              }
            })()
          : { error: { message: 'Upstream provider error' } },
        result.status as HttpStatus,
      );
    }

    if (typeof result.body === 'string') {
      res.setHeader('Content-Type', 'application/json');
      res.status(result.status).json(JSON.parse(result.body));
      return;
    }

    await pipeAiProxySseResponse({
      res,
      upstreamBody: result.body,
      includeMetadata: Boolean(body.founder_os_metadata),
      requestId: route.requestId,
      status: result.status,
      tier: result.tier,
      provider: result.provider,
      model: result.model,
    });

  }
}
