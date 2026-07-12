import { Body, Controller, Post } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { IntentEngineService } from './intent-engine.service';

@Controller('intent')
export class IntentEngineController {
  constructor(private readonly intent: IntentEngineService) {}

  /**
   * POST /api/intent/decompose
   * Body: {
   *   goal: string,
   *   projectId?: string,
   *   maxSteps?: number,
   *   executeFirstStep?: boolean,  // safe filesystem inspect only
   *   cwd?: string
   * }
   */
  @Post('decompose')
  decompose(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      goal?: string;
      projectId?: string;
      maxSteps?: number;
      executeFirstStep?: boolean;
      cwd?: string;
    },
  ) {
    return this.intent.decomposeGoal({
      userId: user.id,
      goal: body.goal ?? '',
      projectId: body.projectId,
      maxSteps: body.maxSteps,
      executeFirstStep: body.executeFirstStep === true,
      cwd: body.cwd,
    });
  }
}
