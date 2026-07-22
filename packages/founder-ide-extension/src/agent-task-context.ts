import { AsyncLocalStorage } from 'node:async_hooks';

const founderTaskContext = new AsyncLocalStorage<string>();

export function currentFounderTaskId(): string | undefined {
  return founderTaskContext.getStore();
}

export function runWithFounderTask<T>(taskId: string, work: () => T): T {
  return founderTaskContext.run(taskId, work);
}
