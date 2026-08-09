import { Body, Controller, Get, Post, UseGuards } from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { AdminGuard, JwtAuthGuard } from '../auth/guards';
import { SecondBrainCallerGuard } from './second-brain-caller.guard';
import {
  SecondBrainService,
  type SecondBrainCritiqueResult,
  type SecondBrainKeyStatus,
} from './second-brain.service';

class CritiqueBodyDto {
  agentOutput!: string;
  context?: string;
  /** Premium GLM last-resort — default false so cheap cascade is preferred. */
  allowGlmSpend?: boolean;
}

/**
 * Product + admin surface for Second Brain.
 * Never routes DeepSeek (Builder / Platform Brain only).
 *
 * Critique spends platform keys — authenticated via Founder Node or JWT
 * (SecondBrainCallerGuard). Status returns booleans only (no secrets).
 */
@Controller('second-brain')
export class SecondBrainController {
  constructor(private readonly secondBrain: SecondBrainService) {}

  /** Which cascade keys are present (booleans only — no secrets). */
  @Public()
  @Get('status')
  status(): Promise<SecondBrainKeyStatus> {
    return this.secondBrain.getKeyStatus();
  }

  /**
   * Founder IDE / team: critique via cheap cascade.
   * @Public so global JWT does not 401 FounderNode auth before our guard runs;
   * SecondBrainCallerGuard still requires Node or Bearer.
   */
  @Public()
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  @UseGuards(SecondBrainCallerGuard)
  @Post('critique')
  critique(@Body() body: CritiqueBodyDto): Promise<SecondBrainCritiqueResult> {
    return this.secondBrain.critique({
      agentOutput: String(body.agentOutput ?? ''),
      context: body.context,
      allowGlmSpend: Boolean(body?.allowGlmSpend),
    });
  }

  /** Admin smoke test — Gemini → OpenAI → optional GLM. Asserts never DeepSeek. */
  @UseGuards(JwtAuthGuard, AdminGuard)
  @Post('test')
  test(@Body() body?: { allowGlmSpend?: boolean }) {
    return this.secondBrain.testCascade({ allowGlmSpend: Boolean(body?.allowGlmSpend) });
  }
}
