# Founder Managed Quota Contract

## Product rule

Founder-managed AI is a recurring allowance measured in weighted token units
(WTU), not a one-time bucket of raw tokens. The V1 default is 200,000 WTU per
seven-day window for an eligible verified founder. BYOK and local inference do
not consume this allowance.

The `founder-wtu-v1` weights are:

- uncached input: 1 WTU
- cached input: 0.25 WTU
- visible output: 3 WTU
- reasoning output: 4 WTU

## Authority and request lifecycle

The API is authoritative. A client display is informational and cannot grant
usage. Before the Founder AI gateway contacts a platform provider, it writes an
`AiManagedReservation` inside a per-user advisory-locked transaction.

1. `RESERVED`: the maximum estimated request cost counts against the window.
2. `RECONCILED`: provider usage replaced the estimate with actual WTU.
3. `RELEASED`: a request rejected before provider execution costs zero.
4. `UNCERTAIN`: an upstream timeout or incomplete stream retains the full
   reservation because provider cost may have occurred.

Request IDs are unique, so retries cannot reserve twice. A stale reservation is
released only when the provider was never started. Missing usage metadata is
charged at the reserved amount.

## Eligibility and denial

Managed usage requires an enabled program, a registered and X-verified user,
and at least one configured platform credential. The API rejects requests that
would exceed the remaining WTU before the provider call. The denial includes
the current recurring window and remaining allowance for a truthful client UI.

## Legacy boundary

Founder IDE and `/api/v1` Founder AI calls use the reservation ledger. Older
BuilderService website copilot paths still obtain promo keys through
`resolvePromoApiKey()` and log usage afterward as `platform_promo`. Stage 5
must consolidate those calls onto the gateway before the platform can claim a
single universal reservation path. Admin views label this distinction.

## Release order and proof

Deploy the Prisma migration before or atomically with the API image. Release
evidence must include:

- schema validation and generated client
- reservation idempotency and pre-provider 429 tests
- streaming and non-streaming reconciliation tests
- full API and Founder IDE extension suites
- an authenticated staging smoke that records request ID, alias, provider,
  model, status, WTU reserved, WTU reconciled, and latency without secrets
