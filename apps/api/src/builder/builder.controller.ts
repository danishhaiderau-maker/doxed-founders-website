import { Body, Controller, Delete, Get, Param, Patch, Post } from '@nestjs/common';
import { AiProvider, ControlPlaneMode, MemoryStorageMode } from '@prisma/client';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { GitHubApiService } from '../github/github-api.service';
import { BuilderService } from './builder.service';

@Controller('builder')
export class BuilderController {
  constructor(
    private readonly builder: BuilderService,
    private readonly github: GitHubApiService,
  ) {}

  @Get('settings')
  settings(@CurrentUser() user: AuthUser) {
    return this.builder.getSettings(user.id);
  }

  @Patch('settings')
  updateSettings(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      defaultProvider?: AiProvider;
      preferredModel?: string;
      autoCreateGitHubIssues?: boolean;
      autoPublishOnEvent?: boolean;
      autopilotEnabled?: boolean;
      autopilotRedeployHosts?: boolean;
      controlPlaneMode?: ControlPlaneMode;
      currentGoalFocus?: string;
      memoryStorageMode?: MemoryStorageMode;
    },
  ) {
    return this.builder.updateSettings(user.id, body);
  }

  @Post('providers/openhands-connect')
  connectOpenHands(
    @CurrentUser() user: AuthUser,
    @Body() body: { baseUrl: string; apiKey: string },
  ) {
    return this.builder.connectOpenHands(user.id, body.baseUrl, body.apiKey);
  }

  @Post('providers/cursor-connect')
  connectCursor(@CurrentUser() user: AuthUser, @Body() body: { apiKey: string }) {
    return this.builder.connectCursor(user.id, body.apiKey);
  }

  @Post('openhands/dispatch')
  dispatchOpenHands(
    @CurrentUser() user: AuthUser,
    @Body() body: { spec: string; cursorPrompt?: string; repository?: string },
  ) {
    return this.builder.dispatchOpenHandsBuildTask(user.id, body);
  }

  @Post('cursor/dispatch')
  dispatchCursor(
    @CurrentUser() user: AuthUser,
    @Body() body: { spec: string; cursorPrompt?: string; repository?: string },
  ) {
    return this.builder.dispatchCursorBuildTask(user.id, body);
  }

  @Post('execute-task')
  executeTask(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      spec: string;
      cursorPrompt?: string;
      repository?: string;
      worker?: 'CURSOR' | 'OPENHANDS';
    },
  ) {
    return this.builder.executeBuildTask(user.id, body);
  }

  @Get('cursor/runs/:agentId/:runId')
  cursorRun(
    @CurrentUser() user: AuthUser,
    @Param('agentId') agentId: string,
    @Param('runId') runId: string,
  ) {
    return this.builder.getCursorRunSnapshot(user.id, agentId, runId);
  }

  @Get('worker-status')
  workerStatus(@CurrentUser() user: AuthUser) {
    return this.builder.getWorkerStatus(user.id);
  }

  @Post('providers/connect')
  connectProvider(@CurrentUser() user: AuthUser, @Body() body: { provider: string; apiKey: string }) {
    return this.builder.connectAiProvider(user.id, body.provider, body.apiKey);
  }

  @Post('providers/ollama-connect')
  connectOllama(
    @CurrentUser() user: AuthUser,
    @Body() body: { baseUrl: string; model?: string },
  ) {
    return this.builder.connectOllama(user.id, body.baseUrl, body.model);
  }

  @Post('providers/phala-connect')
  connectPhala(
    @CurrentUser() user: AuthUser,
    @Body() body: { apiKey: string; inferenceUrl?: string; model?: string },
  ) {
    return this.builder.connectPhala(user.id, body.apiKey, body.inferenceUrl, body.model);
  }

  @Post('providers/:provider/disconnect')
  disconnectProvider(@CurrentUser() user: AuthUser, @Param('provider') provider: string) {
    return this.builder.disconnectAiProvider(user.id, provider);
  }

  @Post('github-token')
  connectGitHubToken(@CurrentUser() user: AuthUser, @Body() body: { token: string }) {
    return this.github.verifyAndStoreToken(user.id, body.token);
  }

  @Delete('github-token')
  disconnectGitHubToken(@CurrentUser() user: AuthUser) {
    return this.github.clearToken(user.id);
  }
}
