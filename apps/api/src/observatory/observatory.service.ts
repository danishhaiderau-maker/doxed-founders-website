import { Injectable } from '@nestjs/common';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RegulatoryClass, SimulatedRaiseStatus } from '@prisma/client';
import { isObservatoryEnabled, isPhase15TrustLayerEnabled } from '../phase15/phase15.constants';
import { PrismaService } from '../prisma/prisma.service';
import { FounderOsService } from '../founder-os/founder-os.service';

export type ObservatorySubsystemRow = {
  id: string;
  label: string;
  description: string;
  status: 'green' | 'yellow' | 'red' | 'unknown';
  version: string | null;
  latencyMs: number | null;
  lastError: string | null;
  lastTest: {
    name: string;
    passed: boolean;
    ranAt: string;
    detail?: string;
  } | null;
  coverage: string | null;
};

type SubsystemConfig = {
  id: string;
  label: string;
  description: string;
  probe: string;
};

let lastSmokeReport: {
  ok: boolean;
  ranAt: string;
  checks: { name: string; passed: boolean; detail: string }[];
} | null = null;

@Injectable()
export class ObservatoryService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly founderOs: FounderOsService,
  ) {}

  static setLastSmokeReport(report: typeof lastSmokeReport) {
    lastSmokeReport = report;
  }

  isEnabled(): boolean {
    return isObservatoryEnabled();
  }

  getVersion(): string {
    return (
      process.env.RAILWAY_GIT_COMMIT_SHA?.slice(0, 7) ??
      process.env.GIT_COMMIT?.slice(0, 7) ??
      process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 7) ??
      process.env.npm_package_version ??
      '0.1.0'
    );
  }

  loadRegistry(): SubsystemConfig[] {
    const path = join(process.cwd(), 'config', 'observatory', 'subsystems.json');
    try {
      return JSON.parse(readFileSync(path, 'utf8')) as SubsystemConfig[];
    } catch {
      return [];
    }
  }

  async getOverview() {
    if (!this.isEnabled()) {
      return { enabled: false, message: 'OBSERVATORY_ENABLED is not true' };
    }

    const started = Date.now();
    const registry = this.loadRegistry();
    const version = this.getVersion();
    const rows: ObservatorySubsystemRow[] = [];

    for (const sub of registry) {
      const row = await this.probeSubsystem(sub, version);
      rows.push(row);
    }

    return {
      enabled: true,
      version,
      gitSha: process.env.RAILWAY_GIT_COMMIT_SHA ?? process.env.GIT_COMMIT ?? null,
      phase15Enabled: isPhase15TrustLayerEnabled(),
      latencyMs: Date.now() - started,
      subsystems: rows,
      lastSmoke: lastSmokeReport,
    };
  }

  private smokeCheckForProbe(probe: string) {
    if (!lastSmokeReport) return null;
    const map: Record<string, string> = {
      'health.api': 'demo_mode_enabled',
      'health.database': 'projects_list_has_demo',
      'demo.status': 'demo_users_seeded',
      'trust.regulatory': 'regulatory_gate_enforced',
      'trust.launch-qualification': 'launch_qualification_api',
      'raise-room.active': 'simulated_raises_active',
      'trust-center.reports': 'trust_validation_signals',
      'founder-os.integrations': 'founder_os_integrations_health',
    };
    const checkName = map[probe];
    if (!checkName) return null;
    const check = lastSmokeReport.checks.find((c) => c.name === checkName);
    if (!check) return null;
    return {
      name: check.name,
      passed: check.passed,
      ranAt: lastSmokeReport.ranAt,
      detail: check.detail,
    };
  }

  private async probeSubsystem(sub: SubsystemConfig, version: string): Promise<ObservatorySubsystemRow> {
    const t0 = Date.now();
    let status: ObservatorySubsystemRow['status'] = 'unknown';
    let lastError: string | null = null;
    let coverage: string | null = null;

    try {
      switch (sub.probe) {
        case 'health.api':
          status = 'green';
          coverage = 'GET /health';
          break;
        case 'health.database': {
          await this.prisma.$queryRaw`SELECT 1`;
          status = 'green';
          coverage = 'Prisma SELECT 1';
          break;
        }
        case 'demo.status': {
          const users = await this.prisma.user.count();
          status = users >= 0 ? 'green' : 'yellow';
          coverage = `${users} users visible`;
          break;
        }
        case 'ddollar.ledger': {
          const rows = await this.prisma.pointLedger.count();
          status = rows > 0 ? 'green' : 'yellow';
          coverage = `${rows} ledger rows`;
          break;
        }
        case 'ai-runtime.status':
          status = process.env.AI_RUNTIME_ENABLED === 'true' ? 'green' : 'yellow';
          coverage = process.env.AI_RUNTIME_ENABLED === 'true' ? 'Phase 1 enabled' : 'Phase 0 pilot';
          break;
        case 'events.status':
          status = 'yellow';
          coverage = 'FounderEvent feed only — domain bus pending';
          break;
        case 'trust.regulatory': {
          if (!isPhase15TrustLayerEnabled()) {
            status = 'yellow';
            coverage = 'PHASE_15_TRUST_LAYER_ENABLED=false';
          } else {
            const classified = await this.prisma.project.count({
              where: { regulatoryClass: { not: RegulatoryClass.PENDING } },
            });
            status = classified > 0 ? 'green' : 'yellow';
            coverage = `${classified} classified projects`;
          }
          break;
        }
        case 'trust.launch-qualification': {
          const scored = await this.prisma.project.count({
            where: { launchQualificationScore: { gt: 0 } },
          });
          status = scored > 0 ? 'green' : 'yellow';
          coverage = `${scored} projects with LQ score`;
          break;
        }
        case 'raise-room.active': {
          const raises = await this.prisma.simulatedRaise.count({
            where: { status: SimulatedRaiseStatus.ACTIVE },
          });
          status = raises > 0 ? 'green' : 'yellow';
          coverage = `${raises} active raises`;
          break;
        }
        case 'trust-center.reports': {
          const reports = await this.prisma.projectTrustReport.count();
          status = reports >= 3 ? 'green' : 'yellow';
          coverage = `${reports} trust reports`;
          break;
        }
        case 'founder-os.integrations': {
          const integrations = await this.founderOs.getIntegrationProviders();
          status = Array.isArray(integrations) && integrations.length > 0 ? 'green' : 'yellow';
          coverage = `${Array.isArray(integrations) ? integrations.length : 0} providers`;
          break;
        }
        default:
          status = 'unknown';
      }
    } catch (err) {
      status = 'red';
      lastError = err instanceof Error ? err.message : 'Probe failed';
    }

    return {
      id: sub.id,
      label: sub.label,
      description: sub.description,
      status,
      version,
      latencyMs: Date.now() - t0,
      lastError,
      lastTest: this.smokeCheckForProbe(sub.probe),
      coverage,
    };
  }
}
