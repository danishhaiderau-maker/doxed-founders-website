import { Injectable, Logger } from '@nestjs/common';
import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import type {
  ProviderEgressBoundary,
  ProviderEgressBudgetDomain,
  ProviderEgressCallSiteId,
  ProviderEgressContext,
  ProviderEgressEvent,
  ProviderEgressSnapshot,
} from './provider-egress-audit.types';
import { isProviderEgressEnforcementStrict } from './founder-ai-runtime.config';

const MAX_AUDIT_EVENTS = 1_000;
const RECENT_EVENT_LIMIT = 100;

type RecordEgressInput = {
  adapterName: string;
  provider: string;
  boundary?: ProviderEgressBoundary;
  callSiteId?: ProviderEgressCallSiteId;
  budgetDomain?: ProviderEgressBudgetDomain;
  runtimeExecutionId?: string;
};

/**
 * In-memory policy telemetry at the provider trust boundary.
 *
 * The audit intentionally records identifiers only. Prompts, response bodies,
 * API keys, file contents, and absolute paths never enter this service.
 */
@Injectable()
export class ProviderEgressAuditService {
  private readonly logger = new Logger(ProviderEgressAuditService.name);
  private readonly context = new AsyncLocalStorage<ProviderEgressContext>();
  private readonly events: ProviderEgressEvent[] = [];

  runWithContext<T>(
    input: Omit<ProviderEgressContext, 'runtimeExecutionId'> & {
      runtimeExecutionId?: string;
    },
    work: () => Promise<T>,
  ): Promise<T> {
    const parent = this.context.getStore();
    const next: ProviderEgressContext = {
      ...input,
      runtimeExecutionId:
        input.runtimeExecutionId ??
        parent?.runtimeExecutionId ??
        randomUUID(),
    };
    return this.context.run(next, work);
  }

  /**
   * Keep one audit context active for the full lifetime of a lazy stream.
   * Async generators do not execute their body when created, so every
   * iterator operation must re-enter the same context.
   */
  wrapAsyncGeneratorWithContext<TYield, TReturn, TNext = unknown>(
    input: Omit<ProviderEgressContext, 'runtimeExecutionId'> & {
      runtimeExecutionId?: string;
    },
    iterator: AsyncGenerator<TYield, TReturn, TNext>,
  ): AsyncGenerator<TYield, TReturn, TNext> {
    const parent = this.context.getStore();
    const runtimeExecutionId =
      input.runtimeExecutionId ??
      parent?.runtimeExecutionId ??
      randomUUID();
    const context = { ...input, runtimeExecutionId };
    const run = <T>(work: () => Promise<T>) =>
      this.runWithContext(context, work);

    return {
      next: (...args: [] | [TNext]) =>
        run(() => iterator.next(...args)),
      return: (value: TReturn | PromiseLike<TReturn>) =>
        run(() => iterator.return(value)),
      throw: (error?: unknown) =>
        run(() => iterator.throw(error)),
      [Symbol.asyncIterator]() {
        return this;
      },
    } as AsyncGenerator<TYield, TReturn, TNext>;
  }

  record(input: RecordEgressInput): ProviderEgressEvent {
    const active = this.context.getStore();
    const boundary = active?.boundary ?? input.boundary ?? 'unscoped';
    const callSiteId = active?.callSiteId ?? input.callSiteId;
    if (!callSiteId) {
      throw new Error(
        'Provider egress requires a typed callSiteId or an active runtime context.',
      );
    }
    const budgetDomain =
      active?.budgetDomain ??
      input.budgetDomain ??
      'unattributed_legacy';
    const runtimeExecutionId =
      active?.runtimeExecutionId ??
      input.runtimeExecutionId ??
      randomUUID();

    const event: ProviderEgressEvent = {
      adapterName: input.adapterName,
      provider: input.provider,
      boundary,
      callSiteId,
      budgetDomain,
      runtimeExecutionId,
      timestamp: new Date().toISOString(),
    };
    this.events.push(event);
    if (this.events.length > MAX_AUDIT_EVENTS) {
      this.events.splice(0, this.events.length - MAX_AUDIT_EVENTS);
    }

    if (boundary === 'unscoped') {
      const message =
        `Unscoped provider egress callSite=${callSiteId} ` +
        `adapter=${input.adapterName} provider=${input.provider}`;
      this.logger.warn(message);
      if (isProviderEgressEnforcementStrict()) {
        throw new Error(
          `${message}. Route this call through FounderAiRuntimeService or register an explicit approved exception.`,
        );
      }
    }
    return event;
  }

  snapshot(): ProviderEgressSnapshot {
    const counts = {
      founderRuntime: 0,
      ideProxyRuntime: 0,
      managedAuxiliary: 0,
      approvedExceptions: 0,
      bypassed: 0,
    };
    const byCallSite: Record<string, number> = {};
    const unscopedCallSites = new Set<string>();

    for (const event of this.events) {
      byCallSite[event.callSiteId] =
        (byCallSite[event.callSiteId] ?? 0) + 1;
      if (event.boundary === 'founder_ai_runtime') {
        counts.founderRuntime += 1;
      } else if (event.boundary === 'ai_proxy_runtime') {
        counts.ideProxyRuntime += 1;
      } else if (event.boundary === 'managed_auxiliary') {
        counts.managedAuxiliary += 1;
      } else if (event.boundary === 'approved_exception') {
        counts.approvedExceptions += 1;
      } else {
        counts.bypassed += 1;
        unscopedCallSites.add(event.callSiteId);
      }
    }

    const governed =
      counts.founderRuntime +
      counts.ideProxyRuntime +
      counts.managedAuxiliary;
    const policyCalls = governed + counts.bypassed;
    return {
      total: this.events.length,
      governed,
      ...counts,
      governedCoverageRatio:
        policyCalls > 0 ? governed / policyCalls : null,
      founderRuntimeCoverageRatio:
        policyCalls > 0 ? counts.founderRuntime / policyCalls : null,
      byCallSite,
      unscopedCallSites: [...unscopedCallSites].sort(),
      recent: this.events.slice(-RECENT_EVENT_LIMIT),
    };
  }

  reset(): void {
    this.events.length = 0;
  }
}
