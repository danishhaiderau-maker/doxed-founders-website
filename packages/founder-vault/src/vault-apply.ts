import fs from 'node:fs';
import { vaultFilePath } from './paths.js';
import { emptyTasksFile, parseTasksJson, type FounderVaultMeta } from './schema.js';
import { defaultProjectContext } from './snapshot.js';

export function applyPushGoal(vaultRoot: string, goal: string): void {
  const trimmed = goal.trim();
  if (!trimmed) return;

  const metaPath = vaultFilePath(vaultRoot, 'meta');
  const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8')) as FounderVaultMeta;
  const projectName = meta.projectName?.trim() || 'My Project';

  const tasksPath = vaultFilePath(vaultRoot, 'tasks');
  let tasksFile = emptyTasksFile(trimmed);
  if (fs.existsSync(tasksPath)) {
    const parsed = parseTasksJson(fs.readFileSync(tasksPath, 'utf8'));
    if (parsed) {
      tasksFile = {
        ...parsed,
        currentGoal: trimmed,
        updatedAt: new Date().toISOString(),
      };
    }
  }
  fs.writeFileSync(tasksPath, JSON.stringify(tasksFile, null, 2), 'utf8');
  fs.writeFileSync(
    vaultFilePath(vaultRoot, 'projectContext'),
    defaultProjectContext(projectName, trimmed),
    'utf8',
  );

  meta.updatedAt = new Date().toISOString();
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf8');
}

export function applyPushTask(
  vaultRoot: string,
  input: { title: string; taskId?: string },
): void {
  const title = input.title.trim();
  if (!title) return;

  const tasksPath = vaultFilePath(vaultRoot, 'tasks');
  const tasksFile = fs.existsSync(tasksPath)
    ? parseTasksJson(fs.readFileSync(tasksPath, 'utf8')) ?? emptyTasksFile('Define your next milestone')
    : emptyTasksFile('Define your next milestone');

  const id = input.taskId?.trim() || `task_${Date.now().toString(36)}`;
  if (tasksFile.tasks.some((t) => t.id === id)) return;

  tasksFile.tasks.unshift({
    id,
    title,
    status: 'open',
    kind: 'task',
    done: false,
  });
  tasksFile.updatedAt = new Date().toISOString();
  fs.writeFileSync(tasksPath, JSON.stringify(tasksFile, null, 2), 'utf8');
}
