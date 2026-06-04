import { assertCvmUnwrapPurpose, type SecretUnwrapPurpose } from '@dcf/utils';

export type CvmUnwrapRequest = {
  encryptedToken: string;
  provider: string;
  purpose: SecretUnwrapPurpose;
  userId: string;
  workloadId?: string | null;
};

export type CvmUnwrapResult = {
  ok: boolean;
  plaintext: string | null;
  unwrapPath: 'cvm_sealed' | 'platform_encrypted';
  error: string | null;
  receipt: Record<string, unknown> | null;
};

export function resolvePlatformCvmUnwrapUrl(env: NodeJS.ProcessEnv = process.env): string | null {
  return env.PHALA_CVM_UNWRAP_URL?.trim() || null;
}

export function resolvePlatformCvmApiKey(env: NodeJS.ProcessEnv = process.env): string | null {
  return env.PHALA_CVM_API_KEY?.trim() || env.PHALA_API_KEY?.trim() || null;
}

export async function unwrapCredentialViaCvm(input: {
  unwrapUrl: string;
  apiKey: string;
  payload: CvmUnwrapRequest;
}): Promise<CvmUnwrapResult> {
  assertCvmUnwrapPurpose(input.payload.purpose);

  const base = input.unwrapUrl.replace(/\/$/, '');
  const path = base.endsWith('/secrets/unwrap') ? base : `${base}/secrets/unwrap`;

  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${input.apiKey.trim()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        encryptedToken: input.payload.encryptedToken,
        provider: input.payload.provider,
        purpose: input.payload.purpose,
        userId: input.payload.userId,
        workloadId: input.payload.workloadId ?? undefined,
      }),
      signal: AbortSignal.timeout(30_000),
    });

    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;

    if (!res.ok) {
      const errText =
        typeof body?.error === 'string'
          ? body.error
          : typeof body?.message === 'string'
            ? body.message
            : `CVM unwrap HTTP ${res.status}`;
      return {
        ok: false,
        plaintext: null,
        unwrapPath: 'platform_encrypted',
        error: errText,
        receipt: body,
      };
    }

    const plaintext =
      typeof body?.plaintext === 'string'
        ? body.plaintext
        : typeof body?.token === 'string'
          ? body.token
          : null;

    if (!plaintext?.trim()) {
      return {
        ok: false,
        plaintext: null,
        unwrapPath: 'platform_encrypted',
        error: 'CVM unwrap returned no plaintext',
        receipt: body,
      };
    }

    return {
      ok: true,
      plaintext: plaintext.trim(),
      unwrapPath: 'cvm_sealed',
      error: null,
      receipt: body,
    };
  } catch (err) {
    return {
      ok: false,
      plaintext: null,
      unwrapPath: 'platform_encrypted',
      error: err instanceof Error ? err.message : 'CVM unwrap request failed',
      receipt: null,
    };
  }
}
