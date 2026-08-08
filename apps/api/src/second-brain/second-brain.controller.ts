import { Body, Controller, Get, Post, UseGuards } from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
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
 */
@Controller('second-brain')
export class SecondBrainController {
  constructor(private readonly secondBrain: SecondBrainService) {}

  /** Which cascade keys are present (booleans only — no secrets). */
  @Get('status')
  status(): Promise<SecondBrainKeyStatus> {
    return this.secondBrain.getKeyStatus();
  }

  /** Founder IDE / team: critique agent output via cheap cascade. */
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  @Post('critique')
  critique(@Body() body: CritiqueBodyDto): Promise<SecondBrainCritiqueResult> {
    return this.secondBrain.critique({
      agentOutput: String(body.agentOutput ?? ''),
      context: body.context,
      allowGlmSpend: Boolean(body.allowGlmSpend),
    });
  }

  /** Admin smoke test — Gemini → OpenAI → optional GLM. Asserts never DeepSeek. */
  @UseGuards(AdminGuard)
  @Post('test')
  test(@Body() body?: { allowGlmSpend?: boolean }) {
    return this.secondBrain.testCascade({ allowGlmSpend: Boolean(body?.allowGlmSpend) });
  }
}
