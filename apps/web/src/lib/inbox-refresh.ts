/** Cross-component signal to refresh message / notification badges immediately. */
export const DCF_INBOX_REFRESH_EVENT = 'dcf-inbox-refresh';

export function dispatchInboxRefresh() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(DCF_INBOX_REFRESH_EVENT));
}

export function subscribeInboxRefresh(handler: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  window.addEventListener(DCF_INBOX_REFRESH_EVENT, handler);
  return () => window.removeEventListener(DCF_INBOX_REFRESH_EVENT, handler);
}
