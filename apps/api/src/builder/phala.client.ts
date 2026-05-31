export type PhalaCredentialMeta = {
  accountName: string;
  inferenceUrl: string;
  model: string;
};

export const DEFAULT_PHALA_INFERENCE_URL = 'https://api.redpill.ai/v1';
export const DEFAULT_PHALA_MODEL = 'phala/deepseek-chat-v3-0324';

export type PhalaChatResult = {
  text: string;
  requestId: string | null;
  model: string;
  signingAddress: string | null;
};

export type AttestationCheck = {
  name: string;
  ok: boolean;
  detail?: string;
};

export type PhalaAttestationReport = Record<string, unknown>;

export function normalizePhalaBaseUrl(url?: string | null): string {
  const raw = (url?.trim() || DEFAULT_PHALA_INFERENCE_URL).replace(/\/$/, '');
  return raw.endsWith('/v1') ? raw : `${raw}/v1`;
}

export function generatePhalaNonce(): string {
  return randomHex(32);
}

function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
}

function pickSigningAddress(report: PhalaAttestationReport): string | null {
  const top = report.signing_address;
  if (typeof top === 'string' && top.trim()) return top.trim();
  const gateway = report.gateway_attestation as { signing_address?: string } | undefined;
  if (gateway?.signing_address?.trim()) return gateway.signing_address.trim();
  const models = report.model_attestations as Array<{ signing_address?: string }> | undefined;
  if (models?.[0]?.signing_address?.trim()) return models[0].signing_address.trim();
  const all = report.all_attestations as Array<{ signing_address?: string }> | undefined;
  if (all?.[0]?.signing_address?.trim()) return all[0].signing_address.trim();
  return null;
}

function pickIntelQuote(report: PhalaAttestationReport): string | null {
  const top = report.intel_quote;
  if (typeof top === 'string' && top.length > 16) return top;
  const gateway = report.gateway_attestation as { intel_quote?: string } | undefined;
  if (gateway?.intel_quote && gateway.intel_quote.length > 16) return gateway.intel_quote;
  const models = report.model_attestations as Array<{ intel_quote?: string }> | undefined;
  if (models?.[0]?.intel_quote && models[0].intel_quote.length > 16) return models[0].intel_quote;
  const all = report.all_attestations as Array<{ intel_quote?: string }> | undefined;
  if (all?.[0]?.intel_quote && all[0].intel_quote.length > 16) return all[0].intel_quote;
  return null;
}

export function assessPhalaAttestationReport(
  report: PhalaAttestationReport,
  expectedNonce?: string | null,
): { verified: boolean; checks: AttestationCheck[]; signingAddress: string | null } {
  const checks: AttestationCheck[] = [];
  const signingAddress = pickSigningAddress(report);
  const intelQuote = pickIntelQuote(report);

  checks.push({
    name: 'signing_address',
    ok: Boolean(signingAddress),
    detail: signingAddress ?? 'Missing TEE signing address in report',
  });
  checks.push({
    name: 'intel_tdx_quote',
    ok: Boolean(intelQuote),
    detail: intelQuote ? 'Intel TDX quote present' : 'Missing intel_quote',
  });

  const responseNonce =
    (typeof report.request_nonce === 'string' && report.request_nonce) ||
    (typeof report.nonce === 'string' && report.nonce) ||
    null;

  if (expectedNonce) {
    checks.push({
      name: 'nonce_match',
      ok: responseNonce === expectedNonce,
      detail:
        responseNonce === expectedNonce
          ? 'Fresh nonce embedded in TEE report'
          : 'Nonce mismatch — possible replay',
    });
  } else if (responseNonce) {
    checks.push({
      name: 'nonce_present',
      ok: true,
      detail: 'Report includes request nonce',
    });
  }

  const gpu =
    (typeof report.nvidia_payload === 'string' && report.nvidia_payload.length > 2) ||
    Boolean(
      (report.model_attestations as Array<{ nvidia_payload?: string }> | undefined)?.[0]
        ?.nvidia_payload,
    );
  checks.push({
    name: 'gpu_attestation',
    ok: gpu,
    detail: gpu ? 'GPU attestation payload present' : 'GPU payload not included for this model',
  });

  const requiredOk = checks.filter((c) => c.name !== 'gpu_attestation').every((c) => c.ok);
  return { verified: requiredOk, checks, signingAddress };
}

export async function verifyPhalaConnection(input: {
  apiKey: string;
  inferenceUrl?: string | null;
}): Promise<{ ok: true } | { ok: false; reason: string }> {
  const baseUrl = normalizePhalaBaseUrl(input.inferenceUrl);
  try {
    const res = await fetch(`${baseUrl}/models`, {
      headers: { Authorization: `Bearer ${input.apiKey.trim()}` },
      signal: AbortSignal.timeout(12_000),
    });
    if (res.ok) return { ok: true };
    const ping = await fetch(`${baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${input.apiKey.trim()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: DEFAULT_PHALA_MODEL,
        messages: [{ role: 'user', content: 'ping' }],
        max_tokens: 8,
      }),
      signal: AbortSignal.timeout(15_000),
    });
    if (ping.ok) return { ok: true };
    return { ok: false, reason: `Phala API rejected key (HTTP ${res.status})` };
  } catch (err) {
    return {
      ok: false,
      reason: err instanceof Error ? err.message : 'Could not reach Phala inference URL',
    };
  }
}

export async function callPhalaChat(input: {
  apiKey: string;
  inferenceUrl?: string | null;
  model?: string | null;
  system: string;
  userPrompt: string;
}): Promise<PhalaChatResult | null> {
  const baseUrl = normalizePhalaBaseUrl(input.inferenceUrl);
  const model = input.model?.trim() || DEFAULT_PHALA_MODEL;
  const res = await fetch(`${baseUrl}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${input.apiKey.trim()}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: input.system },
        { role: 'user', content: input.userPrompt },
      ],
      temperature: 0.4,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Phala HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
  }
  const data = (await res.json()) as { id?: string; choices?: { message?: { content?: string } }[] };
  const text = data.choices?.[0]?.message?.content ?? null;
  if (!text?.trim()) return null;

  const signingHeader =
    res.headers.get('x-signing-address') ??
    res.headers.get('x-phala-signing-address') ??
    res.headers.get('signing-address');

  return {
    text: text.trim(),
    requestId: data.id ?? null,
    model,
    signingAddress: signingHeader?.trim() || null,
  };
}

export async function fetchPhalaAttestationReport(input: {
  apiKey: string;
  inferenceUrl?: string | null;
  model: string;
  nonce?: string;
  signingAddress?: string | null;
}): Promise<PhalaAttestationReport> {
  const baseUrl = normalizePhalaBaseUrl(input.inferenceUrl);
  const params = new URLSearchParams({ model: input.model });
  if (input.nonce) params.set('nonce', input.nonce);
  if (input.signingAddress?.trim()) params.set('signing_address', input.signingAddress.trim());

  const res = await fetch(`${baseUrl}/attestation/report?${params.toString()}`, {
    headers: { Authorization: `Bearer ${input.apiKey.trim()}` },
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Attestation report HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
  }
  return (await res.json()) as PhalaAttestationReport;
}

export async function fetchPhalaRequestSignature(input: {
  apiKey: string;
  inferenceUrl?: string | null;
  requestId: string;
  model: string;
}): Promise<Record<string, unknown> | null> {
  const baseUrl = normalizePhalaBaseUrl(input.inferenceUrl);
  const params = new URLSearchParams({ model: input.model });
  const res = await fetch(`${baseUrl}/signature/${encodeURIComponent(input.requestId)}?${params}`, {
    headers: { Authorization: `Bearer ${input.apiKey.trim()}` },
    signal: AbortSignal.timeout(20_000),
  });
  if (!res.ok) return null;
  return (await res.json()) as Record<string, unknown>;
}
