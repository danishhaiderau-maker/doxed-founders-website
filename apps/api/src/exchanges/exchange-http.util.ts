export async function exchangeFetch(
  url: string,
  init: RequestInit = {},
  timeoutMs = 8000,
): Promise<Response> {
  return fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

export function exchangeErrorMessage(err: unknown, label: string): string {
  const msg = err instanceof Error ? err.message : String(err);
  return `${label} unreachable: ${msg}`;
}
