import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import {
  AiProvider,
  ComputePlaneMode,
  ControlPlaneMode,
  MemoryStorageMode,
  OnboardingPath,
  Prisma,
  SecretsStorageMode,
} from '@prisma/client';
import {
  AI_PROVIDERS,
  QUICK_BUILD_AI_SYSTEM,
  QuickBuildResult,
  aiProviderConfig,
  buildCursorCloudTaskMessage,
  buildOpenHandsTaskMessage,
  githubRepoToUrl,
  isFounderNodeAiProvider,
  isRemoteAgentProvider,
  processQuickBuild,
  resolveBuildWorker,
  type BuildWorkerKey,
} from '@dcf/utils';
import { SealedCredentialsService } from '../credentials/sealed-credentials.service';
import { FounderNodeInferenceService } from '../founder-node/founder-node-inference.service';
import { FounderNodeSyncService } from '../founder-node/founder-node-sync.service';
import { AttestationService } from '../attestation/attestation.service';
import { GitHubApiService } from '../github/github-api.service';
import { WorkspaceActivityService } from '../github/workspace-activity.service';
import { PrismaService } from '../prisma/prisma.service';
import {
  CursorCredentialMeta,
  dispatchCursorCloudTask,
  fetchCursorRun,
  isCursorRunTerminal,
  verifyCursorCloudConnection,
} from './cursor-cloud.client';
import {
  OpenHandsCredentialMeta,
  dispatchOpenHandsTask,
  fetchOpenHandsConversationSnapshot,
  isOpenHandsRunTerminal,
  verifyOpenHandsConnection,
} from './openhands.client';
import {
  DEFAULT_PHALA_INFERENCE_URL,
  DEFAULT_PHALA_MODEL,
  type PhalaCredentialMeta,
  callPhalaChat,
  normalizePhalaBaseUrl,
  verifyPhalaConnection,
  type PhalaChatResult,
} from './phala.client';
import { callJatevoChat, verifyJatevoConnection } from './jatevo.client';
import { callSurplusChat, verifySurplusConnection } from './surplus.client';
import {
  estimateLlmTokensFromText,
  parseAnthropicUsage,
  parseOpenAiStyleUsage,
  buildFounderBrainProviderOrder,
  classifyFounderBrainTask,
  type FounderBrainTask,
} from '@dcf/utils';
import { PlatformAdoptionService } from '../projects/platform-adoption.service';
import { FounderMemoryGraphService } from '../founder-memory/founder-memory-graph.service';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';
import { AgentRuntimeService } from './agent-runtime.service';
import { isAgentRunActive } from '@dcf/utils';
import {
  FounderPromoService,
  GLM_PROMO_BASE_URL,
  GLM_PROMO_DEFAULT_MODEL,
  type PromoCredentialProvider,
} from '../founder-os/founder-promo.service';

type LlmUsage = { promptTokens: number; completionTokens: number };

/** Map an AiProvider to its promo credential provider key, or null if not a promo provider. */
function promoProviderForAi(provider: AiProvider): PromoCredentialProvider | null {
  switch (provider) {
    case AiProvider.GLM:
      return 'glm';
    case AiProvider.GEMINI:
      return 'gemini';
    case AiProvider.DEEPSEEK:
      return 'deepseek';
    default:
      return null;
  }
}

/** Inverse of `promoProviderForAi` — promo credential key back to AiProvider. */
function aiProviderForPromoProvider(p: PromoCredentialProvider): AiProvider {
  if (p === 'glm') return AiProvider.GLM;
  if (p === 'gemini') return AiProvider.GEMINI;
  return AiProvider.DEEPSEEK;
}

@Injectable()
export class BuilderService {
  private readonly logger = new Logger(BuilderService.name);
  constructor(
    private readonly prisma: PrismaService,
    private readonly sealed: SealedCredentialsService,
    private readonly github: GitHubApiService,
    private readonly workspaceActivity: WorkspaceActivityService,
    private readonly founderNodeInference: FounderNodeInferenceService,
    private readonly founderNodeSync: FounderNodeSyncService,
    private readonly attestation: AttestationService,
    private readonly adoption: PlatformAdoptionService,
    private readonly memoryGraph: FounderMemoryGraphService,
    private readonly agentRuns: FounderAgentRunService,
    private readonly agentRuntime: AgentRuntimeService,
    private readonly founderPromo: FounderPromoService,
  ) {}

  async getSecretsStatus(userId: string) {
    return this.sealed.getStatus(userId);
  }

  private async secretsStorageModeFor(userId: string): Promise<SecretsStorageMode | undefined> {
    const row = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { secretsStorageMode: true },
    });
    return row?.secretsStorageMode;
  }

  async getSettings(userId: string) {
    await this.reconcileDefaultBrain(userId);
    const settings = await this.ensureSettings(userId);
    const connected = await this.listConnectedProviders(userId);
    const promoKeys = await this.promoCredentialProviders(userId);

    const openHandsMeta = await this.getOpenHandsMeta(userId);
    const cursorMeta = await this.getCursorMeta(userId);
    const ollamaStatus = await this.founderNodeInference.getOllamaStatus(userId);
    const phalaStatus = await this.getPhalaPrivateAiStatus(userId);
    const secretsStatus = await this.sealed.getStatus(userId);
    const founderNodeV2 = await this.founderNodeSync.getV2Status(userId);
    const githubConn = await this.prisma.gitHubConnection.findUnique({
      where: { userId },
      select: { repoFullName: true, accessTokenEncrypted: true },
    });
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      select: { githubRepoFullName: true },
    });
    const repoFullName =
      githubConn?.repoFullName ?? founder?.githubRepoFullName ?? null;

    const readyBrains = await this.listConnectedBrainProviders(userId);
    const defaultBrainConnected =
      settings.defaultProvider === AiProvider.RULE_BASED ||
      readyBrains.includes(settings.defaultProvider);

    return {
      defaultProvider: settings.defaultProvider,
      defaultBrainConnected,
      connectedBrainCount: readyBrains.length,
      preferredModel: settings.preferredModel,
      autoCreateGitHubIssues: settings.autoCreateGitHubIssues,
      autoPublishOnEvent: settings.autoPublishOnEvent,
      autopilotEnabled: settings.autopilotEnabled,
      autopilotRedeployHosts: settings.autopilotRedeployHosts,
      controlPlaneMode: settings.controlPlaneMode,
      onboardingPath: settings.onboardingPath,
      computePlaneMode: settings.computePlaneMode,
      starterPack: settings.starterPack,
      currentGoalFocus: settings.currentGoalFocus,
      memoryStorageMode: settings.memoryStorageMode,
      secretsStorageMode: settings.secretsStorageMode,
      secretsStatus,
      openHandsBaseUrl: openHandsMeta?.baseUrl ?? null,
      cursorAgentUrl: cursorMeta?.agentId ? `https://cursor.com/agents/${cursorMeta.agentId}` : null,
      founderNodeAi: ollamaStatus,
      founderNodeV2,
      phalaPrivateAi: phalaStatus,
      providers: AI_PROVIDERS.map((p) => {
        const cred = p.credentialProvider;
        const byok = cred ? connected.has(cred) : false;
        const promo = cred ? promoKeys.has(cred) : false;
        const isConnected =
          p.connectMode === 'none'
            ? p.key === 'RULE_BASED'
            : p.key === 'PHALA'
              ? phalaStatus.ready
              : p.connectMode === 'founder_node'
                ? ollamaStatus.ollamaReady
                : p.key === 'CURSOR'
                  ? byok || promo
                  : byok || promo;
        return {
          ...p,
          connected: isConnected,
          billingSource: promo && !byok ? ('platform_promo' as const) : ('byok' as const),
        };
      }),
      githubTokenConnected: Boolean(githubConn?.accessTokenEncrypted),
      repoFullName:
        repoFullName && !String(repoFullName).endsWith('/pending-setup') ? repoFullName : null,
    };
  }

  async updateSettings(
    userId: string,
    input: {
      defaultProvider?: AiProvider;
      preferredModel?: string;
      autoCreateGitHubIssues?: boolean;
      autoPublishOnEvent?: boolean;
      autopilotEnabled?: boolean;
      autopilotRedeployHosts?: boolean;
      controlPlaneMode?: ControlPlaneMode;
      onboardingPath?: OnboardingPath;
      computePlaneMode?: ComputePlaneMode;
      starterPack?: string | null;
      currentGoalFocus?: string;
      memoryStorageMode?: 'PLATFORM' | 'GITHUB' | 'LOCAL_DEVICE' | 'LOCAL_SYNC' | 'FOUNDER_NODE';
      secretsStorageMode?: SecretsStorageMode;
    },
  ) {
    if (input.defaultProvider) {
      const cfg = aiProviderConfig(input.defaultProvider);
      if (!cfg) throw new BadRequestException('Unknown provider');
      if (cfg.connectMode === 'founder_node') {
        const ready = await this.founderNodeInference.isOllamaReady(userId);
        if (!ready) {
          throw new BadRequestException(
            'Pair Founder Node with Ollama running locally, or connect a direct Ollama URL first',
          );
        }
      } else if (input.defaultProvider === AiProvider.PHALA) {
        const phala = await this.resolvePhalaCredentials(userId);
        if (!phala) {
          throw new BadRequestException(
            'Connect Phala Private AI or enable platform Phala credits before setting as default',
          );
        }
      } else if (cfg.needsApiKey || cfg.connectMode === 'remote_agent') {
        const cred = cfg.credentialProvider
          ? await this.prisma.integrationCredential.findUnique({
              where: { userId_provider: { userId, provider: cfg.credentialProvider } },
            })
          : null;
        if (!cred?.token) {
          throw new BadRequestException(`Connect ${cfg.label} before setting as default`);
        }
        if (cfg.connectMode === 'remote_agent' && cfg.key === 'OPENHANDS') {
          const meta = cred.metadata as OpenHandsCredentialMeta | null;
          if (!meta?.baseUrl) {
            throw new BadRequestException('Connect OpenHands base URL before setting as default');
          }
        }
      }
    }

    const settings = await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        defaultProvider: input.defaultProvider ?? AiProvider.RULE_BASED,
        preferredModel: input.preferredModel,
        autoCreateGitHubIssues: input.autoCreateGitHubIssues ?? false,
        autoPublishOnEvent: input.autoPublishOnEvent ?? false,
        autopilotEnabled: input.autopilotEnabled ?? false,
        autopilotRedeployHosts: input.autopilotRedeployHosts ?? false,
        controlPlaneMode: input.controlPlaneMode ?? ControlPlaneMode.FULL_STACK,
        currentGoalFocus: input.currentGoalFocus,
        memoryStorageMode: input.memoryStorageMode ?? MemoryStorageMode.PLATFORM,
        secretsStorageMode: input.secretsStorageMode ?? SecretsStorageMode.PLATFORM_ENCRYPTED,
      },
      update: {
        ...(input.defaultProvider !== undefined ? { defaultProvider: input.defaultProvider } : {}),
        ...(input.preferredModel !== undefined ? { preferredModel: input.preferredModel } : {}),
        ...(input.autoCreateGitHubIssues !== undefined
          ? { autoCreateGitHubIssues: input.autoCreateGitHubIssues }
          : {}),
        ...(input.autoPublishOnEvent !== undefined
          ? { autoPublishOnEvent: input.autoPublishOnEvent }
          : {}),
        ...(input.autopilotEnabled !== undefined
          ? { autopilotEnabled: input.autopilotEnabled }
          : {}),
        ...(input.autopilotRedeployHosts !== undefined
          ? { autopilotRedeployHosts: input.autopilotRedeployHosts }
          : {}),
        ...(input.controlPlaneMode !== undefined
          ? { controlPlaneMode: input.controlPlaneMode }
          : {}),
        ...(input.onboardingPath !== undefined ? { onboardingPath: input.onboardingPath } : {}),
        ...(input.computePlaneMode !== undefined
          ? { computePlaneMode: input.computePlaneMode }
          : {}),
        ...(input.starterPack !== undefined ? { starterPack: input.starterPack } : {}),
        ...(input.currentGoalFocus !== undefined ? { currentGoalFocus: input.currentGoalFocus } : {}),
        ...(input.memoryStorageMode !== undefined
          ? { memoryStorageMode: input.memoryStorageMode as MemoryStorageMode }
          : {}),
        ...(input.secretsStorageMode !== undefined
          ? { secretsStorageMode: input.secretsStorageMode }
          : {}),
      },
    });

    if (input.currentGoalFocus !== undefined) {
      await this.founderNodeSync.maybeEnqueueGoalPush(userId, input.currentGoalFocus);
    }

    return this.getSettings(userId).then(() => ({
      defaultProvider: settings.defaultProvider,
      preferredModel: settings.preferredModel,
      autoCreateGitHubIssues: settings.autoCreateGitHubIssues,
    }));
  }

  async connectAiProvider(userId: string, provider: string, apiKey: string) {
    const cfg = AI_PROVIDERS.find((p) => p.credentialProvider === provider);
    if (
      !cfg?.needsApiKey ||
      cfg.connectMode === 'remote_agent' ||
      cfg.connectMode === 'founder_node' ||
      provider === 'phala'
    ) {
      throw new BadRequestException('Use OpenHands, Cursor, Ollama, or Phala connect for this provider');
    }

    const key = apiKey.trim();
    if (!key) throw new BadRequestException('API key required');

    const verified = await this.verifyAiKey(provider, key);
    const encrypted = this.sealed.encryptForStore(key);
    const storageMode = await this.secretsStorageModeFor(userId);
    const sealedMeta = this.sealed.sealMetadata(
      provider,
      {
        accountName: verified.accountName,
        model: cfg.defaultModel,
      },
      storageMode,
    );

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider } },
      create: {
        userId,
        provider,
        token: encrypted,
        metadata: sealedMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: sealedMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });
    await this.sealed.audit(userId, provider, 'store', 'connect_verify');

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider } },
      create: {
        userId,
        provider,
        connected: true,
        label: cfg.label,
        metadata: { accountName: verified.accountName } as Prisma.InputJsonValue,
      },
      update: { connected: true, metadata: { accountName: verified.accountName } as Prisma.InputJsonValue },
    });

    const brain = cfg.key ? await this.promoteDefaultBrain(userId, cfg.key as AiProvider) : null;

    return {
      success: true,
      provider,
      accountName: verified.accountName,
      brainActivated: brain,
    };
  }

  async connectOllama(userId: string, baseUrl: string, model?: string) {
    const url = baseUrl.trim().replace(/\/+$/, '');
    if (!url) throw new BadRequestException('Ollama base URL required');

    await this.verifyOllamaUrl(url);

    const storageMode = await this.secretsStorageModeFor(userId);
    const ollamaMeta = this.sealed.sealMetadata(
      'ollama',
      {
        accountName: 'Ollama (direct URL)',
        baseUrl: url,
        model: model?.trim() || 'llama3.2',
      },
      storageMode,
    );
    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'ollama' } },
      create: {
        userId,
        provider: 'ollama',
        token: this.sealed.encryptForStore('local'),
        metadata: ollamaMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        metadata: ollamaMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });
    await this.sealed.audit(userId, 'ollama', 'store', 'connect_verify');

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'ollama' } },
      create: {
        userId,
        provider: 'ollama',
        connected: true,
        label: 'Ollama (local)',
        metadata: { baseUrl: url } as Prisma.InputJsonValue,
      },
      update: { connected: true, metadata: { baseUrl: url } as Prisma.InputJsonValue },
    });

    const brain = await this.promoteDefaultBrain(userId, AiProvider.OLLAMA_LOCAL, model?.trim());

    return {
      success: true,
      accountName: 'Ollama (direct URL)',
      baseUrl: url,
      brainActivated: brain,
    };
  }

  async connectPhala(
    userId: string,
    apiKey: string,
    inferenceUrl?: string,
    model?: string,
  ) {
    const key = apiKey.trim();
    if (!key) throw new BadRequestException('Phala API key required');

    const normalizedUrl = normalizePhalaBaseUrl(
      inferenceUrl?.trim() || process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL,
    );
    const resolvedModel = model?.trim() || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL;

    const verified = await verifyPhalaConnection({ apiKey: key, inferenceUrl: normalizedUrl });
    if (!verified.ok) throw new BadRequestException(verified.reason);

    const encrypted = this.sealed.encryptForStore(key);
    const storageMode = await this.secretsStorageModeFor(userId);
    const metadata = this.sealed.sealMetadata(
      'phala',
      {
        accountName: 'Phala Private AI',
        inferenceUrl: normalizedUrl,
        model: resolvedModel,
      } satisfies PhalaCredentialMeta,
      storageMode,
    );

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'phala' } },
      create: {
        userId,
        provider: 'phala',
        token: encrypted,
        metadata: metadata as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: metadata as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });
    await this.sealed.audit(userId, 'phala', 'store', 'connect_verify');

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'phala' } },
      create: {
        userId,
        provider: 'phala',
        connected: true,
        label: 'Private AI (Phala)',
        metadata: { inferenceUrl: normalizedUrl, model: resolvedModel } as Prisma.InputJsonValue,
      },
      update: {
        connected: true,
        metadata: { inferenceUrl: normalizedUrl, model: resolvedModel } as Prisma.InputJsonValue,
      },
    });

    const brain = await this.promoteDefaultBrain(userId, AiProvider.PHALA, resolvedModel);

    return {
      success: true,
      accountName: metadata.accountName,
      inferenceUrl: normalizedUrl,
      model: resolvedModel,
      brainActivated: brain,
    };
  }

  async connectOpenHands(userId: string, baseUrl: string, apiKey: string) {
    const url = baseUrl.trim();
    const key = apiKey.trim();
    if (!url || !key) throw new BadRequestException('OpenHands base URL and API key required');

    const verified = await verifyOpenHandsConnection(url, key);
    const encrypted = this.sealed.encryptForStore(key);
    const normalized = url.replace(/\/+$/, '');
    const storageMode = await this.secretsStorageModeFor(userId);
    const openHandsMeta = this.sealed.sealMetadata(
      'openhands',
      {
        baseUrl: normalized,
        accountName: verified.accountName,
        apiVersion: verified.apiVersion,
      },
      storageMode,
    );

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'openhands' } },
      create: {
        userId,
        provider: 'openhands',
        token: encrypted,
        metadata: openHandsMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: openHandsMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });
    await this.sealed.audit(userId, 'openhands', 'store', 'connect_verify');

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'openhands' } },
      create: {
        userId,
        provider: 'openhands',
        connected: true,
        label: 'OpenHands',
        metadata: { baseUrl: normalized, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
      update: {
        connected: true,
        metadata: { baseUrl: normalized, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
    });

    return {
      success: true,
      accountName: verified.accountName,
      baseUrl: normalized,
      apiVersion: verified.apiVersion,
    };
  }

  async connectCursor(userId: string, apiKey: string) {
    const key = apiKey.trim();
    if (!key) throw new BadRequestException('Cursor API key required');

    const verified = await verifyCursorCloudConnection(key);
    const encrypted = this.sealed.encryptForStore(key);
    const existing = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    const prevMeta = (existing?.metadata as CursorCredentialMeta | null) ?? {};
    const storageMode = await this.secretsStorageModeFor(userId);
    const cursorMeta = this.sealed.sealMetadata(
      'cursor',
      {
        accountName: verified.accountName,
        agentId: prevMeta.agentId ?? null,
        agentRepoUrl: prevMeta.agentRepoUrl ?? null,
        latestRunId: prevMeta.latestRunId ?? null,
      },
      storageMode,
    );

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'cursor' } },
      create: {
        userId,
        provider: 'cursor',
        token: encrypted,
        metadata: cursorMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: cursorMeta as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });
    await this.sealed.audit(userId, 'cursor', 'store', 'connect_verify');

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'cursor' } },
      create: {
        userId,
        provider: 'cursor',
        connected: true,
        label: 'Cursor Cloud Agents',
        metadata: { accountName: verified.accountName, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
      update: {
        connected: true,
        metadata: { accountName: verified.accountName, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
    });

    return {
      success: true,
      accountName: verified.accountName,
      agentUrl: prevMeta.agentId ? `https://cursor.com/agents/${prevMeta.agentId}` : null,
    };
  }

  async getWorkspaceActivity(userId: string, repository?: string | null) {
    return this.workspaceActivity.getActivity(userId, repository);
  }

  async dispatchCursorBuildTask(
    userId: string,
    input: { spec: string; cursorPrompt?: string; repository?: string },
  ) {
    const resolved = await this.resolveLlmApiKey(userId, 'cursor');
    if (!resolved) {
      const promo = await this.founderPromo.getUserPromoStatus(userId);
      if (promo.enabled && promo.founderRegistered && !promo.eligible) {
        throw new BadRequestException(this.founderPromo.promoEndedMessage(promo));
      }
      throw new BadRequestException('Connect Cursor in AI Stack first — or use your founder promo if active');
    }

    const { apiKey, billingSource } = resolved;
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    const meta = (cred?.metadata as CursorCredentialMeta | null) ?? {};

    const activity = await this.workspaceActivity.getActivity(userId, input.repository);
    const workspaceContext = this.workspaceActivity.buildPromptContext(activity);
    const taskPrompt = buildCursorCloudTaskMessage(
      input.spec,
      input.cursorPrompt,
      workspaceContext,
    );
    const repoUrl = input.repository ? githubRepoToUrl(input.repository) : null;
    const startingRef = input.repository
      ? await this.github.getDefaultBranch(userId, input.repository)
      : undefined;

    const result = await dispatchCursorCloudTask({
      apiKey,
      taskPrompt,
      repository: input.repository,
      startingRef,
      agentId: meta.agentId,
      agentRepoUrl: meta.agentRepoUrl,
    });

    if (billingSource === 'platform_promo') {
      await this.logAiTokenUsage(
        userId,
        AiProvider.CURSOR,
        taskPrompt,
        '',
        taskPrompt.slice(0, 500),
        'cursor_promo_dispatch',
        { promptTokens: estimateLlmTokensFromText(taskPrompt), completionTokens: 0 },
        'platform_promo',
      );
    }

    if (cred) {
      await this.prisma.integrationCredential.update({
        where: { userId_provider: { userId, provider: 'cursor' } },
        data: {
          metadata: {
            ...meta,
            agentId: result.agentId,
            agentRepoUrl: repoUrl ?? meta.agentRepoUrl ?? null,
            latestRunId: result.runId,
          } as Prisma.InputJsonValue,
        },
      });
    }

    return result;
  }

  async getBuildWorkerConnections(userId: string) {
    const connected = await this.listConnectedProviders(userId);
    const promoKeys = await this.promoCredentialProviders(userId);
    const settings = await this.ensureSettings(userId);
    const ollamaReady = await this.founderNodeInference.isOllamaReady(userId);
    return {
      cursor: connected.has('cursor') || promoKeys.has('cursor'),
      openHands: connected.has('openhands'),
      founderNode: ollamaReady,
      defaultProvider: settings.defaultProvider,
    };
  }

  async resolveBuildWorker(userId: string): Promise<BuildWorkerKey> {
    const connections = await this.getBuildWorkerConnections(userId);
    return resolveBuildWorker(connections);
  }

  async getOpenHandsRunSnapshot(userId: string, conversationId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'openhands' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect OpenHands in AI Stack first');
    }
    const meta = cred.metadata as OpenHandsCredentialMeta | null;
    if (!meta?.baseUrl) throw new BadRequestException('OpenHands base URL missing — reconnect');
    const apiKey = await this.sealed.unwrap(userId, 'openhands', 'openhands_dispatch');
    if (!apiKey) throw new BadRequestException('OpenHands API key invalid — reconnect');
    const snap = await fetchOpenHandsConversationSnapshot(
      meta.baseUrl,
      apiKey,
      conversationId,
      meta.apiVersion ?? 'v1',
    );
    return {
      ...snap,
      terminal: snap.terminal ?? isOpenHandsRunTerminal(snap.status),
    };
  }

  async getCursorRunSnapshot(
    userId: string,
    agentId: string,
    runId: string,
    repository?: string | null,
  ) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect Cursor in AI Stack first');
    }
    const apiKey = await this.sealed.unwrap(userId, 'cursor', 'cursor_dispatch');
    if (!apiKey) throw new BadRequestException('Cursor API key invalid — reconnect');
    const run = await fetchCursorRun(apiKey, agentId, runId);
    const terminal = isCursorRunTerminal(run.status);
    const activity = await this.workspaceActivity.getActivity(userId, repository);
    const platformReconciliation = terminal
      ? this.workspaceActivity.reconcileRunResult(run.result, activity)
      : null;

    return {
      ...run,
      terminal,
      agentUrl: `https://cursor.com/agents/${agentId}`,
      workspaceActivity: activity,
      platformReconciliation,
    };
  }

  async executeBuildTask(
    userId: string,
    input: {
      spec: string;
      cursorPrompt?: string;
      repository?: string;
      worker?: 'CURSOR' | 'OPENHANDS';
    },
  ) {
    const connections = await this.getBuildWorkerConnections(userId);
    const worker =
      input.worker === 'CURSOR' && connections.cursor
        ? 'CURSOR'
        : input.worker === 'OPENHANDS' && connections.openHands
          ? 'OPENHANDS'
          : await this.resolveBuildWorker(userId);

    if (worker === 'CURSOR') {
      try {
        const result = await this.dispatchCursorBuildTask(userId, input);
        await this.agentRuntime.startRun(userId, {
          adapterId: 'cursor',
          worker: 'CURSOR',
          status: 'CREATING',
          task: input.spec,
          repository: input.repository ?? null,
          agentId: result.agentId,
          runId: result.runId,
        });
        return {
          worker,
          adapterId: 'cursor' as const,
          adapterLabel: 'Builder Agent',
          status: 'dispatched' as const,
          agentUrl: result.agentUrl,
          agentId: result.agentId,
          runId: result.runId,
          mode: result.mode,
          cursorCloud: result,
        };
      } catch (err) {
        return {
          worker,
          status: 'error' as const,
          error: err instanceof Error ? err.message : 'Builder dispatch failed',
        };
      }
    }

    if (worker === 'OPENHANDS') {
      try {
        const result = await this.dispatchOpenHandsBuildTask(userId, input);
        await this.agentRuntime.startRun(userId, {
          adapterId: 'openhands',
          worker: 'OPENHANDS',
          status: 'WORKING',
          task: input.spec,
          repository: input.repository ?? null,
          conversationId: result.conversationId,
        });
        return {
          worker,
          adapterId: 'openhands' as const,
          adapterLabel: 'Builder Agent',
          status: 'dispatched' as const,
          agentUrl: result.conversationUrl,
          conversationId: result.conversationId,
          openHandsApiVersion: result.apiVersion,
          openHands: result,
        };
      } catch (err) {
        return {
          worker,
          status: 'error' as const,
          error: err instanceof Error ? err.message : 'Builder dispatch failed',
        };
      }
    }

    if (worker === 'FOUNDER_NODE') {
      return {
        worker,
        status: 'queued' as const,
        message:
          'Founder Node is connected for chat — use Execute after queuing a spec, or dispatch via Quick Build on desktop.',
      };
    }

    return {
      worker: 'NONE' as const,
      status: 'queued' as const,
      message: 'Task queued. Connect a builder worker in Settings to auto-dispatch remotely.',
    };
  }

  /** Phase 3 — refresh persisted run steps from live worker snapshot. */
  async refreshActiveAgentRun(userId: string) {
    const run = await this.agentRuns.getActive(userId);
    if (!run || !isAgentRunActive(run)) {
      return { run, active: false };
    }

    if (run.worker === 'CURSOR' && run.agentId && run.runId) {
      try {
        const snap = await this.getCursorRunSnapshot(
          userId,
          run.agentId,
          run.runId,
          run.repository,
        );
        const branch = snap.git?.branches?.[0]?.branch ?? null;
        const prUrl = snap.git?.branches?.[0]?.prUrl ?? null;
        const updated = await this.agentRuntime.refreshFromWorkerSnapshot(userId, {
          worker: 'CURSOR',
          status: snap.status,
          branch,
          prUrl,
          terminal: snap.terminal,
        });
        return { run: updated ?? run, active: !snap.terminal };
      } catch {
        return { run, active: true };
      }
    }

    if (run.worker === 'OPENHANDS' && run.conversationId) {
      try {
        const snap = await this.getOpenHandsRunSnapshot(userId, run.conversationId);
        const updated = await this.agentRuntime.refreshFromWorkerSnapshot(userId, {
          worker: 'OPENHANDS',
          status: snap.status,
          terminal: snap.terminal,
        });
        return { run: updated ?? run, active: !snap.terminal };
      } catch {
        return { run, active: true };
      }
    }

    return { run, active: true };
  }

  async getWorkerStatus(userId: string) {
    const connections = await this.getBuildWorkerConnections(userId);
    const worker = resolveBuildWorker(connections);
    const buildWorkerOptions: { key: 'CURSOR' | 'OPENHANDS'; label: string }[] = [];
    if (connections.cursor) buildWorkerOptions.push({ key: 'CURSOR', label: 'Cursor' });
    if (connections.openHands) buildWorkerOptions.push({ key: 'OPENHANDS', label: 'OpenHands' });
    const llmProviders = await this.listUsableLlmCredentialProviders(userId);
    const githubTokenConnected = Boolean(
      await this.prisma.gitHubConnection.findFirst({
        where: { userId, accessTokenEncrypted: { not: null } },
      }),
    );
    const cursorMeta = await this.getCursorMeta(userId);
    const activeRun = await this.agentRuns.getActive(userId);
    return {
      buildWorker: worker,
      buildWorkerOptions,
      connections,
      llmConnected: llmProviders.size > 0,
      githubConnected: githubTokenConnected,
      cursorAgentUrl: cursorMeta?.agentId ? `https://cursor.com/agents/${cursorMeta.agentId}` : null,
      latestRunId: cursorMeta?.latestRunId ?? null,
      cursorAgentId: cursorMeta?.agentId ?? null,
      activeAgentRun: activeRun,
    };
  }

  async dispatchOpenHandsBuildTask(
    userId: string,
    input: { spec: string; cursorPrompt?: string; repository?: string },
  ) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'openhands' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect OpenHands in Settings → Builder first');
    }

    const meta = cred.metadata as OpenHandsCredentialMeta | null;
    if (!meta?.baseUrl) throw new BadRequestException('OpenHands base URL missing — reconnect');

    const apiKey = await this.sealed.unwrap(userId, 'openhands', 'openhands_dispatch');
    if (!apiKey) throw new BadRequestException('OpenHands API key invalid — reconnect');

    const taskPrompt = buildOpenHandsTaskMessage(input.spec, input.cursorPrompt);
    const result = await dispatchOpenHandsTask(
      {
        baseUrl: meta.baseUrl,
        apiKey,
        taskPrompt,
        repository: input.repository,
      },
      meta.apiVersion,
    );

    return result;
  }

  async isOpenHandsDefault(userId: string): Promise<boolean> {
    const settings = await this.ensureSettings(userId);
    return settings.defaultProvider === AiProvider.OPENHANDS;
  }

  async disconnectAiProvider(userId: string, provider: string) {
    await this.prisma.integrationCredential.deleteMany({ where: { userId, provider } });
    await this.prisma.connectedAppStatus.updateMany({
      where: { userId, provider },
      data: { connected: false },
    });

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const cfg = AI_PROVIDERS.find((p) => p.credentialProvider === provider);
    if (settings && cfg && settings.defaultProvider === cfg.key) {
      await this.prisma.founderBuilderSettings.update({
        where: { userId },
        data: { defaultProvider: AiProvider.RULE_BASED },
      });
    }

    return { success: true };
  }

  /** User's key → provider API. Returns null → caller uses rule-based fallback. */
  async tryAiCompletion(
    userId: string,
    system: string,
    userPrompt: string,
  ): Promise<string | null> {
    const result = await this.tryCopilotChatCompletion(userId, system, userPrompt);
    return result.ok ? result.text : null;
  }

  /**
   * Chat completion for Founder Copilot — uses default LLM when set, otherwise any
   * connected API key (DeepSeek, OpenAI, etc.) even if default is Cursor/OpenHands.
   */
  async tryCopilotChatCompletion(
    userId: string,
    system: string,
    userPrompt: string,
    options?: {
      forceProvider?: AiProvider;
      skipMemoryPrefix?: boolean;
      founderBrainTask?: FounderBrainTask;
      userApiKey?: string;
    },
  ): Promise<
    | { ok: true; text: string; provider: AiProvider; founderBrainTask: FounderBrainTask }
    | { ok: false; llmErrors: string[] }
  > {
    let effectiveSystem = system;
    if (!options?.skipMemoryPrefix && !system.includes('Founder Memory Graph')) {
      const prefix = await this.memoryGraph.getPrefixForUser(userId);
      effectiveSystem = `${prefix}${system}`;
    }

    if (options?.forceProvider) {
      const forced = await this.tryCopilotChatCompletionForced(
        userId,
        effectiveSystem,
        userPrompt,
        options.forceProvider,
        options.userApiKey,
      );
      if (forced.ok) {
        return {
          ...forced,
          founderBrainTask: options.founderBrainTask ?? classifyFounderBrainTask(userPrompt),
        };
      }
      return forced;
    }

    const founderBrainTask =
      options?.founderBrainTask ?? classifyFounderBrainTask(userPrompt);

    const settings = await this.ensureSettings(userId);
    const usable = await this.listUsableLlmCredentialProviders(userId);
    const routedKeys = buildFounderBrainProviderOrder(
      founderBrainTask,
      settings.defaultProvider as import('@dcf/utils').AiProviderKey,
    );
    const order = routedKeys as AiProvider[];
    const llmErrors: string[] = [];

    if (settings.defaultProvider === AiProvider.OLLAMA_LOCAL) {
      const nodeResult = await this.founderNodeInference.runViaFounderNode(
        userId,
        effectiveSystem,
        userPrompt,
        settings.preferredModel,
      );
      if (nodeResult.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          effectiveSystem,
          userPrompt,
          nodeResult.text,
          'copilot',
        );
        return { ok: true, text: nodeResult.text, provider: AiProvider.OLLAMA_LOCAL, founderBrainTask };
      }
      llmErrors.push(...nodeResult.errors);

      const direct = await this.tryDirectOllama(
        userId,
        effectiveSystem,
        userPrompt,
        settings.preferredModel,
      );
      if (direct.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          effectiveSystem,
          userPrompt,
          direct.text,
          'copilot',
        );
        return { ok: true, text: direct.text, provider: AiProvider.OLLAMA_LOCAL, founderBrainTask };
      }
      if (direct.error) llmErrors.push(direct.error);
    }

    if (settings.defaultProvider === AiProvider.PHALA) {
      const phala = await this.resolvePhalaCredentials(userId);
      if (phala) {
        try {
          const chat = await callPhalaChat({
            apiKey: phala.apiKey,
            inferenceUrl: phala.inferenceUrl,
            model: settings.preferredModel ?? phala.model,
            system: effectiveSystem,
            userPrompt,
          });
          if (chat?.text) {
            await this.recordPhalaChat(userId, chat);
            await this.logAiTokenUsage(
              userId,
              AiProvider.PHALA,
              effectiveSystem,
              userPrompt,
              chat.text,
              'copilot',
            );
            return { ok: true, text: chat.text, provider: AiProvider.PHALA, founderBrainTask };
          }
          llmErrors.push('PHALA: empty response');
        } catch (err) {
          llmErrors.push(`PHALA: ${err instanceof Error ? err.message : 'request failed'}`);
        }
      } else {
        llmErrors.push('PHALA: connect Phala Private AI or enable platform credits');
      }
    }

    for (const provider of order) {
      const cfg = aiProviderConfig(provider);
      if (!cfg?.credentialProvider) continue;
      if (provider === AiProvider.PHALA) {
        const phala = await this.resolvePhalaCredentials(userId);
        if (!phala) continue;
        const model =
          provider === settings.defaultProvider
            ? settings.preferredModel ?? phala.model
            : phala.model;
        try {
          const chat = await callPhalaChat({
            apiKey: phala.apiKey,
            inferenceUrl: phala.inferenceUrl,
            model,
            system: effectiveSystem,
            userPrompt,
          });
          if (chat?.text) {
            await this.recordPhalaChat(userId, chat);
            await this.logAiTokenUsage(
              userId,
              provider,
              effectiveSystem,
              userPrompt,
              chat.text,
              'copilot',
            );
            return { ok: true, text: chat.text, provider, founderBrainTask };
          }
          llmErrors.push(`${provider}: empty response`);
        } catch (err) {
          llmErrors.push(`${provider}: ${err instanceof Error ? err.message : 'request failed'}`);
        }
        continue;
      }
      if (cfg.connectMode !== 'api_key') continue;

      const resolved = await this.resolveLlmApiKey(userId, cfg.credentialProvider as PromoCredentialProvider);
      if (!resolved) continue;

      const { apiKey, billingSource } = resolved;

      const model =
        provider === settings.defaultProvider
          ? settings.preferredModel ?? cfg.defaultModel ?? undefined
          : cfg.defaultModel ?? undefined;

      try {
        const result = await this.completionWithProvider(
          provider,
          apiKey,
          effectiveSystem,
          userPrompt,
          model,
        );
        if (result?.text?.trim()) {
          await this.logAiTokenUsage(
            userId,
            provider,
            effectiveSystem,
            userPrompt,
            result.text.trim(),
            'copilot',
            result.usage,
            billingSource,
          );
          return { ok: true, text: result.text.trim(), provider, founderBrainTask };
        }
        llmErrors.push(`${provider}: empty response`);
      } catch (err) {
        llmErrors.push(
          `${provider}: ${err instanceof Error ? err.message : 'request failed'}`,
        );
      }
    }

    const platformBrain = await this.tryPlatformDeepseekFallback(userId, effectiveSystem, userPrompt, llmErrors);
    if (platformBrain) {
      return { ok: true, text: platformBrain.text, provider: AiProvider.DEEPSEEK, founderBrainTask };
    }

    const promoStatus = await this.founderPromo.getUserPromoStatus(userId);
    if (promoStatus.enabled && promoStatus.founderRegistered && !promoStatus.eligible) {
      llmErrors.push(this.founderPromo.promoEndedMessage(promoStatus));
    }

    return llmErrors.length > 0 ? { ok: false, llmErrors } : { ok: false, llmErrors: ['No LLM API key configured'] };
  }

  /** Single-provider completion (Social Hub draft buttons). */
  private async tryCopilotChatCompletionForced(
    userId: string,
    system: string,
    userPrompt: string,
    forceProvider: AiProvider,
    userApiKey?: string,
  ): Promise<
    | { ok: true; text: string; provider: AiProvider }
    | { ok: false; llmErrors: string[] }
  > {
    const settings = await this.ensureSettings(userId);
    const llmErrors: string[] = [];

    if (forceProvider === AiProvider.OLLAMA_LOCAL) {
      const nodeResult = await this.founderNodeInference.runViaFounderNode(
        userId,
        system,
        userPrompt,
        settings.preferredModel,
      );
      if (nodeResult.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          system,
          userPrompt,
          nodeResult.text,
          'copilot_forced',
        );
        return { ok: true, text: nodeResult.text, provider: AiProvider.OLLAMA_LOCAL };
      }
      llmErrors.push(...nodeResult.errors);
      const direct = await this.tryDirectOllama(userId, system, userPrompt, settings.preferredModel);
      if (direct.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          system,
          userPrompt,
          direct.text,
          'copilot_forced',
        );
        return { ok: true, text: direct.text, provider: AiProvider.OLLAMA_LOCAL };
      }
      if (direct.error) llmErrors.push(direct.error);
      return { ok: false, llmErrors };
    }

    if (forceProvider === AiProvider.PHALA) {
      const phala = await this.resolvePhalaCredentials(userId);
      if (!phala) return { ok: false, llmErrors: ['PHALA: not connected'] };
      try {
        const chat = await callPhalaChat({
          apiKey: phala.apiKey,
          inferenceUrl: phala.inferenceUrl,
          model: settings.preferredModel ?? phala.model,
          system,
          userPrompt,
        });
        if (chat?.text) {
          await this.recordPhalaChat(userId, chat);
          await this.logAiTokenUsage(
            userId,
            AiProvider.PHALA,
            system,
            userPrompt,
            chat.text,
            'copilot_forced',
          );
          return { ok: true, text: chat.text, provider: AiProvider.PHALA };
        }
        return { ok: false, llmErrors: ['PHALA: empty response'] };
      } catch (err) {
        return {
          ok: false,
          llmErrors: [`PHALA: ${err instanceof Error ? err.message : 'request failed'}`],
        };
      }
    }

    const cfg = aiProviderConfig(forceProvider);
    if (!cfg?.credentialProvider || cfg.connectMode !== 'api_key') {
      return { ok: false, llmErrors: [`${forceProvider}: not available as chat provider`] };
    }

    // BYOK: caller supplied their own API key (e.g. a Z.ai/OpenAI-compatible
    // key pasted in the chat UI). Use it directly — skip BYOK/promo resolution
    // and bill as user_supplied so it never touches platform quota.
    if (userApiKey && userApiKey.trim().length >= 8) {
      const model =
        settings.defaultProvider === forceProvider
          ? settings.preferredModel ?? cfg.defaultModel ?? undefined
          : cfg.defaultModel ?? undefined;
      try {
        const result = await this.completionWithProvider(forceProvider, userApiKey.trim(), system, userPrompt, model);
        if (result?.text?.trim()) {
          await this.logAiTokenUsage(
            userId,
            forceProvider,
            system,
            userPrompt,
            result.text.trim(),
            'copilot_forced',
            result.usage,
            'byok',
          );
          return { ok: true, text: result.text.trim(), provider: forceProvider };
        }
        return { ok: false, llmErrors: [`${forceProvider}: empty response`] };
      } catch (err) {
        return {
          ok: false,
          llmErrors: [`${forceProvider}: ${err instanceof Error ? err.message : 'request failed'}`],
        };
      }
    }

    const resolved = await this.resolveLlmApiKey(userId, cfg.credentialProvider as PromoCredentialProvider);
    if (!resolved) {
      // Promo fallback: when the user explicitly selected a promo provider (e.g. GLM)
      // but has no BYOK key for it, honor the onboarding promo badge ("Doxxed Crypto Promo")
      // by routing through the platform's shared promo keys. The badge is shown whenever
      // ANY promo LLM key is configured, so we may need to fall across to another
      // promo provider (deepseek/gemini) when the selected one has no platform key.
      const promoFallback = await this.tryPromoFallbackForcedCompletion(
        userId,
        system,
        userPrompt,
        forceProvider,
        settings,
      );
      if (promoFallback) return promoFallback;

      const platformBrain = await this.tryPlatformDeepseekFallback(userId, system, userPrompt, llmErrors);
      if (platformBrain) return platformBrain;

      const promoStatus = await this.founderPromo.getUserPromoStatus(userId);
      if (promoStatus.enabled && promoStatus.founderRegistered && !promoStatus.eligible) {
        return { ok: false, llmErrors: [this.founderPromo.promoEndedMessage(promoStatus)] };
      }
      return { ok: false, llmErrors: [`${forceProvider}: connect API key in AI Stack`] };
    }

    const { apiKey, billingSource } = resolved;

    const model =
      settings.defaultProvider === forceProvider
        ? settings.preferredModel ?? cfg.defaultModel ?? undefined
        : cfg.defaultModel ?? undefined;

    try {
      const result = await this.completionWithProvider(forceProvider, apiKey, system, userPrompt, model);
      if (result?.text?.trim()) {
        await this.logAiTokenUsage(
          userId,
          forceProvider,
          system,
          userPrompt,
          result.text.trim(),
          'copilot_forced',
          result.usage,
          billingSource,
        );
        return { ok: true, text: result.text.trim(), provider: forceProvider };
      }
      return { ok: false, llmErrors: [`${forceProvider}: empty response`] };
    } catch (err) {
      return {
        ok: false,
        llmErrors: [`${forceProvider}: ${err instanceof Error ? err.message : 'request failed'}`],
      };
    }
  }

  /** Promo fallback for the forced path.
   *
   * When a user picks a promo provider (GLM/Gemini/DeepSeek) from the model dropdown
   * but has no BYOK key for that exact provider, the onboarding promo badge promises
   * them free LLM access. Honor that promise by routing through the platform's shared
   * promo keys: try the selected provider first, then fall across to the other promo
   * providers (glm → deepseek → gemini) so the call still succeeds when the platform
   * only configured a subset. Returns null when no promo path applies so the caller
   * emits its standard missing-key error. */
  private async tryPromoFallbackForcedCompletion(
    userId: string,
    system: string,
    userPrompt: string,
    forcedProvider: AiProvider,
    settings: { preferredModel?: string | null; defaultProvider?: AiProvider | null },
  ): Promise<{ ok: true; text: string; provider: AiProvider } | { ok: false; llmErrors: string[] } | null> {
    const forcedPromoProvider = promoProviderForAi(forcedProvider);
    if (!forcedPromoProvider) return null; // non-promo provider (OpenAI/Anthropic/etc.) — no fallback

    const promoStatus = await this.founderPromo.getUserPromoStatus(userId);
    if (!promoStatus.eligible) return null;

    const order: PromoCredentialProvider[] = [];
    order.push(forcedPromoProvider);
    for (const p of ['glm', 'deepseek', 'gemini'] as const) {
      if (!order.includes(p)) order.push(p);
    }

    const llmErrors: string[] = [];
    for (const p of order) {
      const promoKey = await this.founderPromo.resolvePromoApiKey(userId, p);
      if (!promoKey) continue;
      const actualProvider = aiProviderForPromoProvider(p);
      const cfg = aiProviderConfig(actualProvider);
      const model =
        settings.defaultProvider === actualProvider
          ? settings.preferredModel ?? cfg?.defaultModel ?? undefined
          : cfg?.defaultModel ?? undefined;
      try {
        const result = await this.completionWithProvider(actualProvider, promoKey, system, userPrompt, model);
        if (result?.text?.trim()) {
          await this.logAiTokenUsage(
            userId,
            actualProvider,
            system,
            userPrompt,
            result.text.trim(),
            'copilot_forced_promo',
            result.usage,
            'platform_promo',
          );
          // Surface the provider the user selected so the UI reads "GLM" (not RULE_BASED).
          return { ok: true, text: result.text.trim(), provider: forcedProvider };
        }
        llmErrors.push(`${actualProvider}: empty response`);
      } catch (err) {
        llmErrors.push(`${actualProvider}: ${err instanceof Error ? err.message : 'request failed'}`);
      }
    }
    return llmErrors.length > 0 ? { ok: false, llmErrors } : null;
  }

  /** Platform brain fallback — uses the platform-managed DeepSeek key (configured via
   * /admin/control) to serve copilot chat when the user has no BYOK key and the promo
   * path is unavailable. Returns null when no platform key is configured so the caller
   * emits its standard missing-key error. */
  private async tryPlatformDeepseekFallback(
    userId: string,
    system: string,
    userPrompt: string,
    existingErrors: string[],
  ): Promise<{ ok: true; text: string; provider: AiProvider } | null> {
    let platformKey: string | null = null;
    try {
      platformKey = await this.founderPromo.getDecryptedPlatformDeepseekKey();
    } catch {
      return null;
    }
    if (!platformKey) return null;

    try {
      const result = await this.completionWithProvider(
        AiProvider.DEEPSEEK,
        platformKey,
        system,
        userPrompt,
        undefined,
      );
      if (result?.text?.trim()) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.DEEPSEEK,
          system,
          userPrompt,
          result.text.trim(),
          'copilot_platform_brain',
          result.usage,
          'platform_brain',
        );
        return { ok: true, text: result.text.trim(), provider: AiProvider.DEEPSEEK };
      }
      existingErrors.push('DEEPSEEK (platform brain): empty response');
    } catch (err) {
      existingErrors.push(
        `DEEPSEEK (platform brain): ${err instanceof Error ? err.message : 'request failed'}`,
      );
    }
    return null;
  }

  private async completionWithProvider(
    provider: AiProvider,
    apiKey: string,
    system: string,
    userPrompt: string,
    model?: string,
  ): Promise<{ text: string; usage: LlmUsage | null } | null> {
    switch (provider) {
      case AiProvider.OPENAI:
        return this.callOpenAi(apiKey, system, userPrompt, model);
      case AiProvider.GLM:
        return this.callGlm(apiKey, system, userPrompt, model);
      case AiProvider.ANTHROPIC:
        return this.callAnthropic(apiKey, system, userPrompt, model);
      case AiProvider.GEMINI:
        return this.callGemini(apiKey, system, userPrompt, model);
      case AiProvider.DEEPSEEK:
        return this.callDeepSeek(apiKey, system, userPrompt, model);
      case AiProvider.OPENROUTER:
        return this.callOpenRouter(apiKey, system, userPrompt, model);
      case AiProvider.JATEVO:
        return this.callJatevo(apiKey, system, userPrompt, model);
      case AiProvider.SURPLUS:
        return this.callSurplus(apiKey, system, userPrompt, model);
      default:
        return null;
    }
  }

  /**
   * Streaming variant of completionWithProvider. Yields text deltas as they
   * arrive from the LLM. Only OpenAI-compatible (DeepSeek, GLM, OpenAI,
   * OpenRouter) and Anthropic + Gemini stream natively; other providers fall
   * back to a single chunk with the full text so the SSE contract still works.
   */
  private async *completionWithProviderStream(
    provider: AiProvider,
    apiKey: string,
    system: string,
    userPrompt: string,
    model?: string,
  ): AsyncGenerator<string, { text: string; usage: LlmUsage | null } | null> {
    switch (provider) {
      case AiProvider.OPENAI:
        return yield* this.streamOpenAiCompatible(
          'https://api.openai.com/v1/chat/completions',
          apiKey,
          system,
          userPrompt,
          model ?? 'gpt-4o-mini',
        );
      case AiProvider.GLM:
        return yield* this.streamOpenAiCompatible(
          `${GLM_PROMO_BASE_URL}/chat/completions`,
          apiKey,
          system,
          userPrompt,
          model ?? GLM_PROMO_DEFAULT_MODEL,
        );
      case AiProvider.DEEPSEEK:
        return yield* this.streamOpenAiCompatible(
          'https://api.deepseek.com/chat/completions',
          apiKey,
          system,
          userPrompt,
          model ?? 'deepseek-chat',
        );
      case AiProvider.OPENROUTER:
        return yield* this.streamOpenAiCompatible(
          'https://openrouter.ai/api/v1/chat/completions',
          apiKey,
          system,
          userPrompt,
          model ?? 'openrouter/auto',
          {
            'HTTP-Referer': 'https://doxxedcrypto.digital',
            'X-Title': 'Doxxed Founder OS',
          },
        );
      case AiProvider.ANTHROPIC:
        return yield* this.streamAnthropic(apiKey, system, userPrompt, model);
      case AiProvider.GEMINI:
        return yield* this.streamGemini(apiKey, system, userPrompt, model);
      default: {
        // Non-streaming providers: emit one chunk with the full text.
        const result = await this.completionWithProvider(
          provider,
          apiKey,
          system,
          userPrompt,
          model,
        );
        if (result?.text) yield result.text;
        return result;
      }
    }
  }

  /** Streams an OpenAI-compatible chat completion (DeepSeek, GLM, OpenAI, OpenRouter). */
  private async *streamOpenAiCompatible(
    url: string,
    apiKey: string,
    system: string,
    userPrompt: string,
    model: string,
    extraHeaders: Record<string, string> = {},
  ): AsyncGenerator<string, { text: string; usage: LlmUsage | null } | null> {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        ...extraHeaders,
      },
      body: JSON.stringify({
        model,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.4,
        stream: true,
        stream_options: { include_usage: true },
      }),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok || !res.body) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let full = '';
    let usage: LlmUsage | null = null;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const rawLine = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!rawLine || !rawLine.startsWith('data:')) continue;
          const payload = rawLine.slice(5).trim();
          if (payload === '[DONE]') continue;
          try {
            const json = JSON.parse(payload) as {
              choices?: { delta?: { content?: string } }[];
              usage?: { prompt_tokens?: number; completion_tokens?: number };
            };
            const delta = json.choices?.[0]?.delta?.content;
            if (delta) {
              full += delta;
              yield delta;
            }
            if (json.usage) usage = parseOpenAiStyleUsage(json);
          } catch {
            // ignore malformed keepalive lines
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    return full ? { text: full, usage } : null;
  }

  /** Streams an Anthropic messages completion. */
  private async *streamAnthropic(
    apiKey: string,
    system: string,
    user: string,
    model?: string,
  ): AsyncGenerator<string, { text: string; usage: LlmUsage | null } | null> {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: model ?? 'claude-3-5-haiku-latest',
        max_tokens: 2048,
        system,
        messages: [{ role: 'user', content: user }],
        stream: true,
      }),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok || !res.body) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let full = '';
    let usage: LlmUsage | null = null;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const rawLine = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!rawLine.startsWith('data:')) continue;
          const payload = rawLine.slice(5).trim();
          try {
            const json = JSON.parse(payload) as {
              type: string;
              delta?: { type: string; text?: string };
              message?: { usage?: { input_tokens?: number; output_tokens?: number } };
              usage?: { input_tokens?: number; output_tokens?: number };
            };
            if (json.type === 'content_block_delta' && json.delta?.text) {
              full += json.delta.text;
              yield json.delta.text;
            }
            if (json.type === 'message_start' && json.message?.usage) {
              usage = parseAnthropicUsage({ usage: json.message.usage });
            }
            if (json.type === 'message_delta' && json.usage) {
              usage = parseAnthropicUsage({ usage: json.usage });
            }
          } catch {
            // ignore
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    return full ? { text: full, usage } : null;
  }

  /** Streams a Gemini generateContent completion (SSE alt format). */
  private async *streamGemini(
    apiKey: string,
    system: string,
    user: string,
    model?: string,
  ): AsyncGenerator<string, { text: string; usage: LlmUsage | null } | null> {
    const modelId = model ?? 'gemini-2.0-flash';
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${modelId}:streamGenerateContent?alt=sse&key=${encodeURIComponent(apiKey)}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: system }] },
        contents: [{ role: 'user', parts: [{ text: user }] }],
      }),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok || !res.body) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let full = '';
    let usage: LlmUsage | null = null;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const rawLine = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!rawLine.startsWith('data:')) continue;
          const payload = rawLine.slice(5).trim();
          try {
            const json = JSON.parse(payload) as {
              candidates?: { content?: { parts?: { text?: string }[] } }[];
              usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number };
            };
            const text = json.candidates?.[0]?.content?.parts?.[0]?.text;
            if (text) {
              full += text;
              yield text;
            }
            if (json.usageMetadata) {
              usage = {
                promptTokens: json.usageMetadata.promptTokenCount ?? 0,
                completionTokens: json.usageMetadata.candidatesTokenCount ?? 0,
              };
            }
          } catch {
            // ignore
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    return full ? { text: full, usage } : null;
  }

  /**
   * Streaming variant of tryCopilotChatCompletion. Resolves provider routing
   * up front, then returns an async generator that yields text deltas. The
   * final return value of the generator is the resolved provider (for
   * attribution). Falls back to non-streaming chunk emission when the chosen
   * provider doesn't support native streaming. Mirrors the routing in
   * tryCopilotChatCompletion so DeepSeek platform fallback + promo keys work.
   */
  async tryCopilotChatCompletionStream(
    userId: string,
    system: string,
    userPrompt: string,
    options?: {
      forceProvider?: AiProvider;
      skipMemoryPrefix?: boolean;
      founderBrainTask?: FounderBrainTask;
      userApiKey?: string;
    },
  ): Promise<{
    ok: true;
    provider: AiProvider;
    founderBrainTask: FounderBrainTask;
    stream: AsyncGenerator<string, AiProvider>;
  } | { ok: false; llmErrors: string[]; founderBrainTask: FounderBrainTask }> {
    let effectiveSystem = system;
    if (!options?.skipMemoryPrefix && !system.includes('Founder Memory Graph')) {
      const prefix = await this.memoryGraph.getPrefixForUser(userId);
      effectiveSystem = `${prefix}${system}`;
    }

    const founderBrainTask =
      options?.founderBrainTask ?? classifyFounderBrainTask(userPrompt);

    if (options?.forceProvider) {
      const forced = await this.tryCopilotChatCompletionForcedStream(
        userId,
        effectiveSystem,
        userPrompt,
        options.forceProvider,
        options.userApiKey,
      );
      if (forced.ok) {
        return { ...forced, founderBrainTask };
      }
      return { ok: false, llmErrors: forced.llmErrors, founderBrainTask };
    }

    const settings = await this.ensureSettings(userId);
    const usable = await this.listUsableLlmCredentialProviders(userId);
    const routedKeys = buildFounderBrainProviderOrder(
      founderBrainTask,
      settings.defaultProvider as import('@dcf/utils').AiProviderKey,
    );
    const order = routedKeys as AiProvider[];
    const llmErrors: string[] = [];

    // Ollama / Phala: stream not wired — fall back to single-chunk non-streaming.
    if (settings.defaultProvider === AiProvider.OLLAMA_LOCAL) {
      const nodeResult = await this.founderNodeInference.runViaFounderNode(
        userId,
        effectiveSystem,
        userPrompt,
        settings.preferredModel,
      );
      if (nodeResult.ok) {
        await this.logAiTokenUsage(userId, AiProvider.OLLAMA_LOCAL, effectiveSystem, userPrompt, nodeResult.text, 'copilot');
        return {
          ok: true,
          provider: AiProvider.OLLAMA_LOCAL,
          founderBrainTask,
          stream: this.singleChunkStream(nodeResult.text, AiProvider.OLLAMA_LOCAL),
        };
      }
      llmErrors.push(...nodeResult.errors);
      const direct = await this.tryDirectOllama(userId, effectiveSystem, userPrompt, settings.preferredModel);
      if (direct.ok) {
        await this.logAiTokenUsage(userId, AiProvider.OLLAMA_LOCAL, effectiveSystem, userPrompt, direct.text, 'copilot');
        return {
          ok: true,
          provider: AiProvider.OLLAMA_LOCAL,
          founderBrainTask,
          stream: this.singleChunkStream(direct.text, AiProvider.OLLAMA_LOCAL),
        };
      }
      if (direct.error) llmErrors.push(direct.error);
    }

    if (settings.defaultProvider === AiProvider.PHALA) {
      const phala = await this.resolvePhalaCredentials(userId);
      if (phala) {
        try {
          const chat = await callPhalaChat({
            apiKey: phala.apiKey,
            inferenceUrl: phala.inferenceUrl,
            model: settings.preferredModel ?? phala.model,
            system: effectiveSystem,
            userPrompt,
          });
          if (chat?.text) {
            await this.recordPhalaChat(userId, chat);
            await this.logAiTokenUsage(userId, AiProvider.PHALA, effectiveSystem, userPrompt, chat.text, 'copilot');
            return {
              ok: true,
              provider: AiProvider.PHALA,
              founderBrainTask,
              stream: this.singleChunkStream(chat.text, AiProvider.PHALA),
            };
          }
          llmErrors.push('PHALA: empty response');
        } catch (err) {
          llmErrors.push(`PHALA: ${err instanceof Error ? err.message : 'request failed'}`);
        }
      } else {
        llmErrors.push('PHALA: connect Phala Private AI or enable platform credits');
      }
    }

    for (const provider of order) {
      const cfg = aiProviderConfig(provider);
      if (!cfg?.credentialProvider) continue;
      if (provider === AiProvider.PHALA) {
        const phala = await this.resolvePhalaCredentials(userId);
        if (!phala) continue;
        const model =
          provider === settings.defaultProvider
            ? settings.preferredModel ?? phala.model
            : phala.model;
        try {
          const chat = await callPhalaChat({
            apiKey: phala.apiKey,
            inferenceUrl: phala.inferenceUrl,
            model,
            system: effectiveSystem,
            userPrompt,
          });
          if (chat?.text) {
            await this.recordPhalaChat(userId, chat);
            await this.logAiTokenUsage(userId, provider, effectiveSystem, userPrompt, chat.text, 'copilot');
            return {
              ok: true,
              provider,
              founderBrainTask,
              stream: this.singleChunkStream(chat.text, provider),
            };
          }
          llmErrors.push(`${provider}: empty response`);
        } catch (err) {
          llmErrors.push(`${provider}: ${err instanceof Error ? err.message : 'request failed'}`);
        }
        continue;
      }
      if (cfg.connectMode !== 'api_key') continue;

      const resolved = await this.resolveLlmApiKey(userId, cfg.credentialProvider as PromoCredentialProvider);
      if (!resolved) continue;
      const { apiKey, billingSource } = resolved;
      const model =
        provider === settings.defaultProvider
          ? settings.preferredModel ?? cfg.defaultModel ?? undefined
          : cfg.defaultModel ?? undefined;

      try {
        const gen = this.completionWithProviderStream(provider, apiKey, effectiveSystem, userPrompt, model);
        const first = await gen.next();
        if (first.done) {
          // No content emitted at all — treat as empty.
          if (first.value?.text) {
            await this.logAiTokenUsage(userId, provider, effectiveSystem, userPrompt, first.value.text, 'copilot', first.value.usage, billingSource);
            return {
              ok: true,
              provider,
              founderBrainTask,
              stream: this.singleChunkStream(first.value.text, provider),
            };
          }
          llmErrors.push(`${provider}: empty response`);
          continue;
        }
        // We have a live stream. Wrap it so the caller keeps consuming and we
        // log usage on completion.
        const firstChunk = first.value as string;
        this.logger.log(
          `stream resolved provider=${provider} billing=${billingSource}` +
            ` model=${model ?? cfg.defaultModel ?? '?'}`,
        );
        return {
          ok: true,
          provider,
          founderBrainTask,
          stream: this.wrapStreamWithUsageLog(
            gen,
            firstChunk,
            userId,
            provider,
            effectiveSystem,
            userPrompt,
            billingSource,
          ),
        };
      } catch (err) {
        llmErrors.push(`${provider}: ${err instanceof Error ? err.message : 'request failed'}`);
      }
    }

    const platformBrain = await this.tryPlatformDeepseekFallbackStream(
      userId,
      effectiveSystem,
      userPrompt,
      llmErrors,
    );
    if (platformBrain) {
      this.logger.log(`stream resolved provider=DEEPSEEK billing=platform_brain (fallback)`);
      return { ok: true, provider: AiProvider.DEEPSEEK, founderBrainTask, stream: platformBrain };
    }

    const promoStatus = await this.founderPromo.getUserPromoStatus(userId);
    if (promoStatus.enabled && promoStatus.founderRegistered && !promoStatus.eligible) {
      llmErrors.push(this.founderPromo.promoEndedMessage(promoStatus));
    }

    return {
      ok: false,
      llmErrors: llmErrors.length > 0 ? llmErrors : ['No LLM API key configured'],
      founderBrainTask,
    };
  }

  /** Single-chunk async generator for non-streaming providers. */
  private async *singleChunkStream(text: string, provider: AiProvider): AsyncGenerator<string, AiProvider> {
    if (text) yield text;
    return provider;
  }

  /** Resume a streaming generator after the first chunk has been pulled. */
  private async *wrapStreamWithUsageLog(
    gen: AsyncGenerator<string, { text: string; usage: LlmUsage | null } | null>,
    firstChunk: string,
    userId: string,
    provider: AiProvider,
    system: string,
    userPrompt: string,
    billingSource: 'byok' | 'platform_promo' | 'platform_brain',
  ): AsyncGenerator<string, AiProvider> {
    yield firstChunk;
    let result: { text: string; usage: LlmUsage | null } | null = null;
    try {
      // Pull remaining chunks.
      while (true) {
        const { done, value } = await gen.next();
        if (done) {
          result = value;
          break;
        }
        yield value as string;
      }
    } finally {
      try {
        await gen.return(null);
      } catch {
        // ignore
      }
    }
    if (result?.text) {
      await this.logAiTokenUsage(
        userId,
        provider,
        system,
        userPrompt,
        result.text,
        'copilot',
        result.usage,
        billingSource,
      );
    }
    return provider;
  }

  /** Forced-provider streaming variant. */
  private async tryCopilotChatCompletionForcedStream(
    userId: string,
    system: string,
    userPrompt: string,
    forceProvider: AiProvider,
    userApiKey?: string,
  ): Promise<
    | { ok: true; provider: AiProvider; stream: AsyncGenerator<string, AiProvider> }
    | { ok: false; llmErrors: string[] }
  > {
    const settings = await this.ensureSettings(userId);
    const llmErrors: string[] = [];

    if (forceProvider === AiProvider.OLLAMA_LOCAL) {
      const nodeResult = await this.founderNodeInference.runViaFounderNode(
        userId,
        system,
        userPrompt,
        settings.preferredModel,
      );
      if (nodeResult.ok) {
        await this.logAiTokenUsage(userId, AiProvider.OLLAMA_LOCAL, system, userPrompt, nodeResult.text, 'copilot_forced');
        return { ok: true, provider: AiProvider.OLLAMA_LOCAL, stream: this.singleChunkStream(nodeResult.text, AiProvider.OLLAMA_LOCAL) };
      }
      llmErrors.push(...nodeResult.errors);
      const direct = await this.tryDirectOllama(userId, system, userPrompt, settings.preferredModel);
      if (direct.ok) {
        await this.logAiTokenUsage(userId, AiProvider.OLLAMA_LOCAL, system, userPrompt, direct.text, 'copilot_forced');
        return { ok: true, provider: AiProvider.OLLAMA_LOCAL, stream: this.singleChunkStream(direct.text, AiProvider.OLLAMA_LOCAL) };
      }
      if (direct.error) llmErrors.push(direct.error);
      return { ok: false, llmErrors };
    }

    if (forceProvider === AiProvider.PHALA) {
      const phala = await this.resolvePhalaCredentials(userId);
      if (!phala) return { ok: false, llmErrors: ['PHALA: not connected'] };
      try {
        const chat = await callPhalaChat({
          apiKey: phala.apiKey,
          inferenceUrl: phala.inferenceUrl,
          model: settings.preferredModel ?? phala.model,
          system,
          userPrompt,
        });
        if (chat?.text) {
          await this.recordPhalaChat(userId, chat);
          await this.logAiTokenUsage(userId, AiProvider.PHALA, system, userPrompt, chat.text, 'copilot_forced');
          return { ok: true, provider: AiProvider.PHALA, stream: this.singleChunkStream(chat.text, AiProvider.PHALA) };
        }
        return { ok: false, llmErrors: ['PHALA: empty response'] };
      } catch (err) {
        return { ok: false, llmErrors: [`PHALA: ${err instanceof Error ? err.message : 'request failed'}`] };
      }
    }

    const cfg = aiProviderConfig(forceProvider);
    if (!cfg?.credentialProvider || cfg.connectMode !== 'api_key') {
      return { ok: false, llmErrors: [`${forceProvider}: not available as chat provider`] };
    }

    // BYOK: caller supplied their own API key. Use it directly, bill as byok.
    if (userApiKey && userApiKey.trim().length >= 8) {
      const byokKey = userApiKey.trim();
      const model =
        settings.defaultProvider === forceProvider
          ? settings.preferredModel ?? cfg.defaultModel ?? undefined
          : cfg.defaultModel ?? undefined;
      try {
        const gen = this.completionWithProviderStream(forceProvider, byokKey, system, userPrompt, model);
        const first = await gen.next();
        if (first.done) {
          if (first.value?.text) {
            await this.logAiTokenUsage(userId, forceProvider, system, userPrompt, first.value.text, 'copilot_forced', first.value.usage, 'byok');
            return { ok: true, provider: forceProvider, stream: this.singleChunkStream(first.value.text, forceProvider) };
          }
          return { ok: false, llmErrors: [`${forceProvider}: empty response`] };
        }
        return {
          ok: true,
          provider: forceProvider,
          stream: this.wrapStreamWithUsageLog(gen, first.value as string, userId, forceProvider, system, userPrompt, 'byok'),
        };
      } catch (err) {
        return { ok: false, llmErrors: [`${forceProvider}: ${err instanceof Error ? err.message : 'request failed'}`] };
      }
    }

    const resolved = await this.resolveLlmApiKey(userId, cfg.credentialProvider as PromoCredentialProvider);
    if (!resolved) {
      // Promo fallback (non-streaming).
      const promoFallback = await this.tryPromoFallbackForcedCompletionStream(
        userId,
        system,
        userPrompt,
        forceProvider,
        settings,
      );
      if (promoFallback) return promoFallback;

      const platformBrain = await this.tryPlatformDeepseekFallbackStream(userId, system, userPrompt, llmErrors);
      if (platformBrain) return { ok: true, provider: AiProvider.DEEPSEEK, stream: platformBrain };

      const promoStatus = await this.founderPromo.getUserPromoStatus(userId);
      if (promoStatus.enabled && promoStatus.founderRegistered && !promoStatus.eligible) {
        return { ok: false, llmErrors: [this.founderPromo.promoEndedMessage(promoStatus)] };
      }
      return { ok: false, llmErrors: [`${forceProvider}: connect API key in AI Stack`] };
    }

    const { apiKey, billingSource } = resolved;
    const model =
      settings.defaultProvider === forceProvider
        ? settings.preferredModel ?? cfg.defaultModel ?? undefined
        : cfg.defaultModel ?? undefined;

    try {
      const gen = this.completionWithProviderStream(forceProvider, apiKey, system, userPrompt, model);
      const first = await gen.next();
      if (first.done) {
        if (first.value?.text) {
          await this.logAiTokenUsage(userId, forceProvider, system, userPrompt, first.value.text, 'copilot_forced', first.value.usage, billingSource);
          this.logger.log(
            `forced stream resolved provider=${forceProvider} billing=${billingSource} model=${model ?? cfg.defaultModel ?? '?'} (single-chunk)`,
          );
          return { ok: true, provider: forceProvider, stream: this.singleChunkStream(first.value.text, forceProvider) };
        }
        return { ok: false, llmErrors: [`${forceProvider}: empty response`] };
      }
      this.logger.log(
        `forced stream resolved provider=${forceProvider} billing=${billingSource} model=${model ?? cfg.defaultModel ?? '?'}`,
      );
      return {
        ok: true,
        provider: forceProvider,
        stream: this.wrapStreamWithUsageLog(gen, first.value as string, userId, forceProvider, system, userPrompt, billingSource),
      };
    } catch (err) {
      return { ok: false, llmErrors: [`${forceProvider}: ${err instanceof Error ? err.message : 'request failed'}`] };
    }
  }

  /** Promo fallback (non-streaming) for the forced stream path. */
  private async tryPromoFallbackForcedCompletionStream(
    userId: string,
    system: string,
    userPrompt: string,
    forcedProvider: AiProvider,
    settings: { preferredModel?: string | null; defaultProvider?: AiProvider | null },
  ): Promise<{ ok: true; provider: AiProvider; stream: AsyncGenerator<string, AiProvider> } | { ok: false; llmErrors: string[] } | null> {
    const forcedPromoProvider = promoProviderForAi(forcedProvider);
    if (!forcedPromoProvider) return null;

    const promoStatus = await this.founderPromo.getUserPromoStatus(userId);
    if (!promoStatus.eligible) return null;

    const order: PromoCredentialProvider[] = [];
    order.push(forcedPromoProvider);
    for (const p of ['glm', 'deepseek', 'gemini'] as const) {
      if (!order.includes(p)) order.push(p);
    }

    const llmErrors: string[] = [];
    for (const p of order) {
      const promoKey = await this.founderPromo.resolvePromoApiKey(userId, p);
      if (!promoKey) continue;
      const actualProvider = aiProviderForPromoProvider(p);
      const cfg = aiProviderConfig(actualProvider);
      const model =
        settings.defaultProvider === actualProvider
          ? settings.preferredModel ?? cfg?.defaultModel ?? undefined
          : cfg?.defaultModel ?? undefined;
      try {
        const gen = this.completionWithProviderStream(actualProvider, promoKey, system, userPrompt, model);
        const first = await gen.next();
        if (first.done) {
          if (first.value?.text) {
            await this.logAiTokenUsage(userId, actualProvider, system, userPrompt, first.value.text, 'copilot_forced_promo', first.value.usage, 'platform_promo');
            return { ok: true, provider: forcedProvider, stream: this.singleChunkStream(first.value.text, forcedProvider) };
          }
          llmErrors.push(`${actualProvider}: empty response`);
          continue;
        }
        return {
          ok: true,
          provider: forcedProvider,
          stream: this.wrapStreamWithUsageLog(gen, first.value as string, userId, actualProvider, system, userPrompt, 'platform_promo'),
        };
      } catch (err) {
        llmErrors.push(`${actualProvider}: ${err instanceof Error ? err.message : 'request failed'}`);
      }
    }
    return llmErrors.length > 0 ? { ok: false, llmErrors } : null;
  }

  /** Platform DeepSeek brain fallback (streaming). */
  private async tryPlatformDeepseekFallbackStream(
    userId: string,
    system: string,
    userPrompt: string,
    existingErrors: string[],
  ): Promise<AsyncGenerator<string, AiProvider> | null> {
    let platformKey: string | null = null;
    try {
      platformKey = await this.founderPromo.getDecryptedPlatformDeepseekKey();
    } catch {
      return null;
    }
    if (!platformKey) return null;

    try {
      const gen = this.completionWithProviderStream(
        AiProvider.DEEPSEEK,
        platformKey,
        system,
        userPrompt,
        undefined,
      );
      const first = await gen.next();
      if (first.done) {
        if (first.value?.text) {
          void this.logAiTokenUsage(userId, AiProvider.DEEPSEEK, system, userPrompt, first.value.text, 'copilot_platform_brain', first.value.usage, 'platform_brain');
          return this.singleChunkStream(first.value.text, AiProvider.DEEPSEEK);
        }
        existingErrors.push('DEEPSEEK (platform brain): empty response');
        return null;
      }
      return this.wrapStreamWithUsageLog(gen, first.value as string, userId, AiProvider.DEEPSEEK, system, userPrompt, 'platform_brain');
    } catch (err) {
      existingErrors.push(`DEEPSEEK (platform brain): ${err instanceof Error ? err.message : 'request failed'}`);
      return null;
    }
  }

  private async logAiTokenUsage(
    userId: string,
    provider: AiProvider | string,
    system: string,
    userPrompt: string,
    text: string,
    source: string,
    usage?: LlmUsage | null,
    billingSource: 'byok' | 'platform_promo' | 'platform_brain' = 'byok',
  ) {
    const promptTokens =
      usage?.promptTokens ?? estimateLlmTokensFromText(`${system}\n${userPrompt}`);
    const completionTokens = usage?.completionTokens ?? estimateLlmTokensFromText(text);
    const projectId = await this.resolvePrimaryProjectId(userId);
    await this.adoption
      .recordAiUsage({
        userId,
        provider: String(provider),
        source,
        promptTokens,
        completionTokens,
        projectId,
        billingSource,
      })
      .catch(() => undefined);
  }

  private async resolvePrimaryProjectId(userId: string): Promise<string | null> {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      select: {
        projects: { where: { approved: true }, take: 1, select: { id: true }, orderBy: { updatedAt: 'desc' } },
      },
    });
    return founder?.projects[0]?.id ?? null;
  }

  private async tryDirectOllama(
    userId: string,
    system: string,
    userPrompt: string,
    preferredModel?: string | null,
  ): Promise<{ ok: true; text: string } | { ok: false; error?: string }> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'ollama' } },
    });
    const meta = cred?.metadata as { baseUrl?: string; model?: string } | null;
    const baseUrl = meta?.baseUrl?.trim();
    if (!baseUrl) return { ok: false };

    try {
      const text = await this.callOllama(
        baseUrl,
        system,
        userPrompt,
        preferredModel?.trim() || meta?.model || 'llama3.2',
      );
      if (text?.trim()) return { ok: true, text: text.trim() };
      return { ok: false, error: 'Ollama returned empty response' };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : 'Direct Ollama request failed',
      };
    }
  }

  async enhanceQuickBuild(userId: string, prompt: string, projectName?: string): Promise<QuickBuildResult> {
    const fallback = processQuickBuild(prompt, projectName);
    const aiText = await this.tryAiCompletion(
      userId,
      QUICK_BUILD_AI_SYSTEM,
      `Project: ${projectName ?? 'startup'}\nFounder request: ${prompt}`,
    );
    if (!aiText) return fallback;

    try {
      const cleaned = aiText.replace(/^```json\s*/i, '').replace(/```\s*$/i, '').trim();
      const parsed = JSON.parse(cleaned) as Partial<QuickBuildResult>;
      return {
        ideaTitle: parsed.ideaTitle ?? fallback.ideaTitle,
        spec: parsed.spec ?? fallback.spec,
        tasks: parsed.tasks?.length ? parsed.tasks : fallback.tasks,
        githubIssues: parsed.githubIssues?.length ? parsed.githubIssues : fallback.githubIssues,
        roadmapTitle: parsed.roadmapTitle ?? fallback.roadmapTitle,
        cursorPrompt: parsed.cursorPrompt ?? fallback.cursorPrompt,
        traderView: parsed.traderView ?? fallback.traderView,
      };
    } catch {
      return fallback;
    }
  }

  private async getOpenHandsMeta(userId: string): Promise<OpenHandsCredentialMeta | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'openhands' } },
    });
    return (cred?.metadata as OpenHandsCredentialMeta | null) ?? null;
  }

  private async getCursorMeta(userId: string): Promise<CursorCredentialMeta | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    return (cred?.metadata as CursorCredentialMeta | null) ?? null;
  }

  private async getPhalaMeta(userId: string): Promise<PhalaCredentialMeta | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
    });
    return (cred?.metadata as PhalaCredentialMeta | null) ?? null;
  }

  private platformPhalaAvailable(): boolean {
    return Boolean(process.env.PHALA_API_KEY?.trim());
  }

  private async getPhalaPrivateAiStatus(userId: string) {
    const meta = await this.getPhalaMeta(userId);
    const userKey = await this.sealed.hasCredential(userId, 'phala');
    const platformAvailable = this.platformPhalaAvailable();
    return {
      ready: userKey || platformAvailable,
      userKeyConnected: userKey,
      platformAvailable,
      inferenceUrl:
        meta?.inferenceUrl ||
        normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL),
      model: meta?.model || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
      docsUrl: 'https://docs.phala.com/phala-cloud/confidential-ai/confidential-model/confidential-ai-api',
    };
  }

  private async recordPhalaChat(userId: string, chat: PhalaChatResult) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    await this.attestation
      .recordPhalaInference(userId, chat, settings?.memoryStorageMode)
      .catch(() => undefined);
  }

  private async resolvePhalaCredentials(userId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
    });
    const userKey = await this.sealed.unwrapPhala(userId, 'phala_inference');
    const meta = (cred?.metadata as PhalaCredentialMeta | null) ?? null;
    if (userKey) {
      return {
        apiKey: userKey,
        inferenceUrl: meta?.inferenceUrl || normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL),
        model: meta?.model || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
        source: 'user' as const,
      };
    }
    const platformKey = process.env.PHALA_API_KEY?.trim();
    if (!platformKey) return null;
    return {
      apiKey: platformKey,
      inferenceUrl: normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL),
      model: process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
      source: 'platform' as const,
    };
  }

  private readonly brainProviderPriority: AiProvider[] = [
    AiProvider.PHALA,
    AiProvider.SURPLUS,
    AiProvider.JATEVO,
    AiProvider.OPENROUTER,
    AiProvider.DEEPSEEK,
    AiProvider.GLM,
    AiProvider.OPENAI,
    AiProvider.ANTHROPIC,
    AiProvider.GEMINI,
    AiProvider.OLLAMA_LOCAL,
  ];

  private async listConnectedBrainProviders(userId: string): Promise<AiProvider[]> {
    const connected = await this.listConnectedProviders(userId);
    const promoKeys = await this.promoCredentialProviders(userId);
    const ollamaReady = await this.founderNodeInference.isOllamaReady(userId);
    const phalaStatus = await this.getPhalaPrivateAiStatus(userId);
    const ready: AiProvider[] = [];

    for (const key of this.brainProviderPriority) {
      const cfg = aiProviderConfig(key);
      if (!cfg) continue;
      if (key === AiProvider.PHALA) {
        if (phalaStatus.ready) ready.push(key);
      } else if (key === AiProvider.OLLAMA_LOCAL) {
        if (ollamaReady) ready.push(key);
      } else if (key === AiProvider.CURSOR) {
        if (connected.has('cursor')) ready.push(key);
      } else if (
        cfg.credentialProvider &&
        (connected.has(cfg.credentialProvider) || promoKeys.has(cfg.credentialProvider))
      ) {
        ready.push(key);
      }
    }
    return ready;
  }

  private async promoCredentialProviders(userId: string): Promise<Set<string>> {
    const status = await this.founderPromo.getUserPromoStatus(userId);
    if (!status.eligible) return new Set();
    const out = new Set<string>();
    for (const p of ['glm', 'gemini', 'deepseek'] as const) {
      if (await this.founderPromo.hasPromoProvider(userId, p)) out.add(p);
    }
    return out;
  }

  /** Pick a connected brain when saved default is rule-based or disconnected. */
  private async reconcileDefaultBrain(userId: string): Promise<void> {
    const settings = await this.ensureSettings(userId);
    const ready = await this.listConnectedBrainProviders(userId);
    if (ready.length === 0) return;

    const storedOk =
      settings.defaultProvider !== AiProvider.RULE_BASED &&
      ready.includes(settings.defaultProvider);
    if (storedOk) return;

    const pick =
      ready.find((k) => k === settings.defaultProvider) ??
      ready[0]!;
    const cfg = aiProviderConfig(pick);
    await this.prisma.founderBuilderSettings.update({
      where: { userId },
      data: {
        defaultProvider: pick,
        ...(!settings.preferredModel && cfg?.defaultModel
          ? { preferredModel: cfg.defaultModel }
          : {}),
      },
    });
  }

  private async promoteDefaultBrain(
    userId: string,
    prefer: AiProvider,
    preferredModelOverride?: string,
  ): Promise<{ defaultProvider: AiProvider; preferredModel: string | null; label: string } | null> {
    const ready = await this.listConnectedBrainProviders(userId);
    if (!ready.includes(prefer)) return null;

    const settings = await this.ensureSettings(userId);
    const cfg = aiProviderConfig(prefer);
    const preferredModel =
      preferredModelOverride?.trim() || cfg?.defaultModel || settings.preferredModel;

    await this.prisma.founderBuilderSettings.update({
      where: { userId },
      data: {
        defaultProvider: prefer,
        ...(preferredModel ? { preferredModel } : {}),
      },
    });

    return {
      defaultProvider: prefer,
      preferredModel: preferredModel ?? null,
      label: cfg?.label ?? prefer,
    };
  }

  private async ensureSettings(userId: string) {
    return this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId },
      update: {},
    });
  }

  private async listConnectedProviders(userId: string) {
    const creds = await this.prisma.integrationCredential.findMany({
      where: { userId, verifiedAt: { not: null } },
      select: { provider: true },
    });
    return new Set(creds.map((c) => c.provider));
  }

  /** User BYOK first; platform promo keys only while eligible (1-month window + token cap).
   *  Accepts promo providers (glm/gemini/deepseek) plus 'cursor' which is BYOK-only. */
  private async resolveLlmApiKey(
    userId: string,
    credentialProvider: PromoCredentialProvider | 'cursor',
  ): Promise<{ apiKey: string; billingSource: 'byok' | 'platform_promo' } | null> {
    let userKey: string | null = null;
    if (credentialProvider === 'cursor') {
      userKey =
        (await this.sealed.unwrap(userId, 'cursor', 'cursor_dispatch')) ??
        (await this.sealed.unwrap(userId, 'cursor', 'copilot_llm'));
    } else {
      userKey = await this.sealed.unwrap(userId, credentialProvider, 'copilot_llm');
    }
    if (userKey?.trim()) {
      return { apiKey: userKey.trim(), billingSource: 'byok' };
    }

    if (credentialProvider === 'cursor') return null;
    const promoKey = await this.founderPromo.resolvePromoApiKey(userId, credentialProvider);
    if (promoKey) {
      return { apiKey: promoKey, billingSource: 'platform_promo' };
    }

    return null;
  }

  /** LLM keys with a stored token (verified optional — chat should still attempt). */
  private async listUsableLlmCredentialProviders(userId: string) {
    const creds = await this.prisma.integrationCredential.findMany({
      where: {
        userId,
        provider: {
          in: ['openai', 'anthropic', 'gemini', 'deepseek', 'openrouter', 'jatevo', 'surplus', 'phala'],
        },
      },
      select: { provider: true, token: true },
    });
    const out = new Set<string>();
    for (const c of creds) {
      if (c.token?.trim()) out.add(c.provider);
    }

    const promoProviders: PromoCredentialProvider[] = ['glm', 'gemini', 'deepseek'];
    for (const p of promoProviders) {
      if (await this.founderPromo.hasPromoProvider(userId, p)) {
        out.add(p);
      }
    }

    return out;
  }

  private async verifyAiKey(provider: string, key: string): Promise<{ accountName: string }> {
    switch (provider) {
      case 'openai': {
        const res = await fetch('https://api.openai.com/v1/models?limit=1', {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) throw new BadRequestException('Invalid OpenAI API key');
        return { accountName: 'OpenAI account' };
      }
      case 'glm': {
        const res = await fetch(`${GLM_PROMO_BASE_URL}/models?limit=1`, {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) throw new BadRequestException('Invalid GLM (ZhipuAI) API key');
        return { accountName: 'GLM 5.2 (ZhipuAI)' };
      }
      case 'anthropic': {
        const res = await fetch('https://api.anthropic.com/v1/messages', {
          method: 'POST',
          headers: {
            'x-api-key': key,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            model: 'claude-3-5-haiku-latest',
            max_tokens: 16,
            messages: [{ role: 'user', content: 'ping' }],
          }),
        });
        if (!res.ok && res.status !== 400) throw new BadRequestException('Invalid Anthropic API key');
        return { accountName: 'Anthropic account' };
      }
      case 'gemini': {
        const res = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(key)}`,
        );
        if (!res.ok) throw new BadRequestException('Invalid Gemini API key');
        return { accountName: 'Google AI account' };
      }
      case 'deepseek': {
        const res = await fetch('https://api.deepseek.com/models', {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) {
          const ping = await fetch('https://api.deepseek.com/chat/completions', {
            method: 'POST',
            headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              model: 'deepseek-chat',
              messages: [{ role: 'user', content: 'ping' }],
              max_tokens: 8,
            }),
          });
          if (!ping.ok) throw new BadRequestException('Invalid DeepSeek API key');
        }
        return { accountName: 'DeepSeek account' };
      }
      case 'openrouter': {
        const res = await fetch('https://openrouter.ai/api/v1/models', {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) throw new BadRequestException('Invalid OpenRouter API key');
        return { accountName: 'OpenRouter account' };
      }
      case 'jatevo': {
        const verified = await verifyJatevoConnection(key);
        if (!verified.ok) throw new BadRequestException(verified.reason);
        return { accountName: verified.accountName };
      }
      case 'surplus': {
        const verified = await verifySurplusConnection(key);
        if (!verified.ok) throw new BadRequestException(verified.reason);
        return { accountName: verified.accountName };
      }
      case 'phala': {
        const verified = await verifyPhalaConnection({ apiKey: key });
        if (!verified.ok) throw new BadRequestException(verified.reason);
        return { accountName: 'Phala Private AI' };
      }
      default:
        throw new BadRequestException(`Unknown AI provider: ${provider}`);
    }
  }

  private async verifyOllamaUrl(baseUrl: string): Promise<void> {
    const res = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new BadRequestException('Cannot reach Ollama at that URL — is it running?');
  }

  private async callOpenAi(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model ?? 'gpt-4o-mini',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    const usage = parseOpenAiStyleUsage(data);
    return { text, usage };
  }

  /** GLM 5.2 (ZhipuAI) — OpenAI-compatible endpoint, cheapest promo LLM. */
  private async callGlm(key: string, system: string, user: string, model?: string) {
    const res = await fetch(`${GLM_PROMO_BASE_URL}/chat/completions`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model ?? GLM_PROMO_DEFAULT_MODEL,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    const usage = parseOpenAiStyleUsage(data);
    return { text, usage };
  }

  private async callAnthropic(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: model ?? 'claude-3-5-haiku-latest',
        max_tokens: 2048,
        system,
        messages: [{ role: 'user', content: user }],
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      content?: { type: string; text?: string }[];
      usage?: { input_tokens?: number; output_tokens?: number };
    };
    const text = data.content?.find((c) => c.type === 'text')?.text;
    if (!text) return null;
    return { text, usage: parseAnthropicUsage(data) };
  }

  private async callGemini(key: string, system: string, user: string, model?: string) {
    const modelId = model ?? 'gemini-2.0-flash';
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${modelId}:generateContent?key=${encodeURIComponent(key)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          systemInstruction: { parts: [{ text: system }] },
          contents: [{ role: 'user', parts: [{ text: user }] }],
        }),
      },
    );
    if (!res.ok) return null;
    const data = (await res.json()) as {
      candidates?: { content?: { parts?: { text?: string }[] } }[];
      usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number };
    };
    const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
    if (!text) return null;
    const usage =
      data.usageMetadata != null
        ? {
            promptTokens: data.usageMetadata.promptTokenCount ?? 0,
            completionTokens: data.usageMetadata.candidatesTokenCount ?? 0,
          }
        : null;
    return { text, usage };
  }

  private async callDeepSeek(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model ?? 'deepseek-chat',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    return { text, usage: parseOpenAiStyleUsage(data) };
  }

  private async callJatevo(key: string, system: string, user: string, model?: string) {
    return callJatevoChat({
      apiKey: key,
      system,
      userPrompt: user,
      model,
    });
  }

  private async callSurplus(key: string, system: string, user: string, model?: string) {
    return callSurplusChat({
      apiKey: key,
      system,
      userPrompt: user,
      model,
    });
  }

  private async callOpenRouter(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://doxxedcrypto.digital',
        'X-Title': 'Doxxed Founder OS',
      },
      body: JSON.stringify({
        model: model ?? 'openrouter/auto',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    return { text, usage: parseOpenAiStyleUsage(data) };
  }

  private async callOllama(baseUrl: string, system: string, user: string, model: string) {
    const res = await fetch(`${baseUrl.replace(/\/+$/, '')}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        stream: false,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
      }),
      signal: AbortSignal.timeout(120_000),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`Ollama HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }
    const data = (await res.json()) as { message?: { content?: string } };
    return data.message?.content ?? null;
  }
}
