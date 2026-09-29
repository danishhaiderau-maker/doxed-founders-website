import type { IdeDispatchStatus } from './api';

export const FOUNDER_IDE_DISPATCH_PROVIDER = 'founder-ide';

export type RemoteDispatchView = {
  label: string;
  pending: boolean;
  terminal: boolean;
  failed: boolean;
  cancellable: boolean;
};

type StatusFields = Pick<IdeDispatchStatus, 'status' | 'executionStatus' | 'result' | 'error' | 'failed' | 'delivered'> &
  Partial<Pick<IdeDispatchStatus, 'cancellationRequested' | 'cancellationReason'>>;

/**
 * Founder IDE remote dispatches report `executionStatus`; legacy Cursor
 * dispatches only report `delivered`/`failed`. Only pending or claimed remote
 * runs without an outstanding cancel request can be cancelled by the owner.
 */
export function remoteDispatchView(s: StatusFields): RemoteDispatchView {
  switch (s.executionStatus) {
    case 'pending':
      return { label: 'Queued for your paired desktop', pending: true, terminal: false, failed: false, cancellable: true };
    case 'claimed':
      return { label: 'Running on your paired desktop', pending: true, terminal: false, failed: false, cancellable: true };
    case 'cancellation_requested':
      return {
        label: 'Cancel requested — waiting for the desktop to stop the run',
        pending: true,
        terminal: false,
        failed: false,
        cancellable: false,
      };
    case 'complete':
      return { label: 'Completed on your paired desktop', pending: false, terminal: true, failed: false, cancellable: false };
    case 'failed':
    case 'expired': {
      const reason = s.error ?? s.result ?? 'unknown';
      return {
        label: /cancel/i.test(reason) ? `Cancelled: ${reason}` : `Failed: ${reason}`,
        pending: false,
        terminal: true,
        failed: true,
        cancellable: false,
      };
    }
    default:
      break;
  }
  if (s.delivered) {
    return { label: 'Delivered to Founder IDE', pending: false, terminal: true, failed: false, cancellable: false };
  }
  if (s.failed) {
    return { label: `Failed: ${s.result ?? 'unknown'}`, pending: false, terminal: true, failed: true, cancellable: false };
  }
  const pending = s.status === 'PENDING' || s.status === 'DISPATCHING';
  return { label: s.status, pending, terminal: false, failed: false, cancellable: false };
}
