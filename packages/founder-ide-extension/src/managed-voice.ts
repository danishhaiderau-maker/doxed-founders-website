import {
  authorizationHeaderFromCredentials,
  resolveCredentials,
} from './credentials';
import {
  transcribeManagedVoiceRequest,
  type ManagedVoiceInput,
  type ManagedVoiceRequestDeps,
  type ManagedVoiceResult,
} from './managed-voice-request';

export type {
  ManagedVoiceInput,
  ManagedVoiceResult,
} from './managed-voice-request';

export async function transcribeManagedVoice(
  input: ManagedVoiceInput,
  deps: ManagedVoiceRequestDeps = {},
): Promise<ManagedVoiceResult> {
  const credentials = resolveCredentials();
  if (!credentials) {
    throw new Error(
      'Connect Founder Node before using Founder managed voice.',
    );
  }

  return transcribeManagedVoiceRequest(
    input,
    {
      apiBaseUrl: credentials.apiBaseUrl,
      authorization: authorizationHeaderFromCredentials(credentials),
    },
    deps,
  );
}
