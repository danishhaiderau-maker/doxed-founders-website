import { authorizationHeaderFromCredentials, resolveCredentials } from './credentials';
import {
  describeManagedVisualRequest,
  type ManagedVisualInput,
  type ManagedVisualRequestDeps,
  type ManagedVisualResult,
} from './managed-visual-request';

export type {
  ManagedVisualAttachment,
  ManagedVisualInput,
  ManagedVisualResult,
} from './managed-visual-request';

export async function describeManagedVisuals(
  input: ManagedVisualInput,
  deps: ManagedVisualRequestDeps = {},
): Promise<ManagedVisualResult> {
  const credentials = resolveCredentials();
  if (!credentials) {
    throw new Error(
      'Founder IDE is not paired. Connect this computer before attaching screenshots.',
    );
  }
  return describeManagedVisualRequest(
    input,
    {
      apiBaseUrl: credentials.apiBaseUrl,
      authorization: authorizationHeaderFromCredentials(credentials),
    },
    deps,
  );
}
