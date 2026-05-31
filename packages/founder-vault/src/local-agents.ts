import fs from 'node:fs';
import { vaultFilePath } from './paths.js';
import { parseTasksJson } from './schema.js';
import { applyPushGoal, applyPushTask } from './vault-apply.js';
import {
  readVaultVectorIndex,
  rebuildVaultVectorIndex,
  searchVaultOnDisk,
  type VaultSearchHit,
} from './vector-index.js';

export type LocalAgentKind = 'vault-index' | 'goal-align' | 'vault-summary';

export type LocalAgentResult = {
  agent: LocalAgentKind;
  ok: boolean;
  summary?: string;
  chunks?: number;
  hits?: VaultSearchHit[];
  localGoal?: string | null;
  cloudGoal?: string | null;
  aligned?: boolean;
};

export function runLocalAgent(
  vaultRoot: string,
  agent: LocalAgentKind,
  payload: Record<string, unknown> = {},
): LocalAgentResult {
  switch (agent) {
    case 'vault-index': {
      const index = rebuildVaultVectorIndex(vaultRoot);
      return {
        agent,
        ok: true,
        chunks: index.chunks.length,
        summary: `Indexed ${index.chunks.length} vault chunks locally`,
      };
    }
    case 'goal-align': {
      const tasksPath = vaultFilePath(vaultRoot, 'tasks');
      const localGoal = fs.existsSync(tasksPath)
        ? parseTasksJson(fs.readFileSync(tasksPath, 'utf8'))?.currentGoal ?? null
        : null;
      const cloudGoal = typeof payload.goal === 'string' ? payload.goal : null;
      const aligned =
        !cloudGoal || !localGoal
          ? true
          : localGoal.trim().toLowerCase() === cloudGoal.trim().toLowerCase();
      return {
        agent,
        ok: true,
        localGoal,
        cloudGoal,
        aligned,
        summary: aligned
          ? 'Local vault goal matches Founder OS'
          : 'Local vault goal differs from Founder OS — pull sync will update on next job',
      };
    }
    case 'vault-summary': {
      const index = readVaultVectorIndex(vaultRoot) ?? rebuildVaultVectorIndex(vaultRoot);
      const query = typeof payload.query === 'string' ? payload.query : 'roadmap tasks launch';
      const hits = searchVaultOnDisk(vaultRoot, query, 3);
      return {
        agent,
        ok: true,
        chunks: index.chunks.length,
        hits,
        summary: `Vault has ${index.chunks.length} indexed chunks; top match: ${hits[0]?.source ?? 'none'}`,
      };
    }
    default:
      return { agent, ok: false, summary: 'Unknown agent' };
  }
}

export function executeSyncJobOnVault(
  vaultRoot: string,
  kind: string,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  switch (kind) {
    case 'PUSH_GOAL':
      applyPushGoal(vaultRoot, String(payload.goal ?? ''));
      return { ok: true, applied: 'PUSH_GOAL', chunks: rebuildVaultVectorIndex(vaultRoot).chunks.length };
    case 'PUSH_TASK':
      applyPushTask(vaultRoot, {
        title: String(payload.title ?? ''),
        taskId: typeof payload.taskId === 'string' ? payload.taskId : undefined,
      });
      return { ok: true, applied: 'PUSH_TASK', chunks: rebuildVaultVectorIndex(vaultRoot).chunks.length };
    case 'VAULT_SEARCH': {
      const query = String(payload.query ?? '').trim();
      const topK = Number(payload.topK ?? 5);
      const hits = searchVaultOnDisk(vaultRoot, query, topK);
      return { ok: true, query, hits };
    }
    case 'RUN_AGENT': {
      const agent = String(payload.agent ?? 'vault-index') as LocalAgentKind;
      const result = runLocalAgent(vaultRoot, agent, payload);
      return { ...result };
    }
    default:
      return { ok: false, error: `Unknown sync job kind: ${kind}` };
  }
}
