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

  record(input: RecordEgressInput): ProviderEgressEvent {
    const active = this.context.getStore();
    const boundary = active?.boundary ?? input.boundary ?? 'unscoped';
    const callSiteId =
      active?.callSiteId ?? input.callSiteId ?? 'ai_routing.other';
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
      this.logger.warn(
        `Unscoped provider egress callSite=${callSiteId} adapter=${input.adapterName} provider=${input.provider}`,
      );
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
      recent: this.events.slice(-RECENT_EVENT_LIMIT),
    };
  }

  reset(): void {
    this.events.length = 0;
  }
}
