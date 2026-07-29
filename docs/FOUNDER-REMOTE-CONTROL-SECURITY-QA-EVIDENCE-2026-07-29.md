# Founder Remote Control Security QA Evidence

Date: 2026-07-29

This evidence covers the local Founder Node pairing, token, IPC, and remote
action contracts. It does not claim that a production website session has
controlled the currently installed Founder IDE.

## Security boundary

The preferred device-code flow is an RFC 8628-style, user-approved exchange:

- The device receives a random 256-bit device code and a separate readable
  eight-character user code.
- The user code expires after 15 minutes, is rate limited, and is consumed
  exactly once.
- Inspecting and authorizing the code requires the signed-in founder account.
- Authorization binds the founder, install identity, bounded device metadata,
  and device fingerprint before a node token can be issued.
- Node tokens are stored as hashes, expire after 30 days, rotate before expiry,
  and can be revoked without exposing a reusable device secret.
- Remote actions route to the exact paired node and IDE session. Workspace
  reads stay inside the active workspace, edits remain proposed until approved,
  and high-risk commands require explicit approval.

The retained legacy `/founder-node/pair` compatibility route is now hardened
to the same ownership expectations:

- Pairing-code format, node identifier, and the optional 32-byte IPC secret are
  validated before expensive or persistent work.
- Label, platform, application version, and install identifier are bounded.
- A node identifier already owned by another founder cannot be transferred.
- Pairing-code consumption and node/settings persistence happen in one
  transaction.
- The transaction claims only an unused, unexpired code. A replay or concurrent
  second claim fails closed.

Implementation commit: `5b760b69`

## Automated evidence

- Founder Node suite: 62 passed, 0 failed.
- Founder Node no-output TypeScript check: passed.
- Focused API manifest, device-code, legacy-pairing, and token-lifecycle suite:
  55 passed, 0 failed.
- Legacy pairing regression: 2 passed, 0 failed.
- Full API suite: 311 passed, 0 failed.
- API no-output TypeScript check: passed.
- Changed-file whitespace check: passed.
- Protected-path audit: zero trading, analyzer, Bitfinex, relay-arm, exchange,
  operator-workflow, or production-gate paths.

The normal emitting API and Founder Node builds were not used as fresh evidence
in this slice because running local application processes hold generated
`dist` files open on Windows. The source-level TypeScript checks passed. No
running application was force-closed and no generated output was deleted to
manufacture a build result.

## State contract

Source and automated contracts cover:

- online and offline device state;
- current workspace identity;
- pending remote approvals;
- last activity;
- rename;
- token rotation;
- revoke;
- reconnect;
- read, proposed edit, accept/reject, command, cancel, streamed output, and
  status messages;
- replay rejection, bounded timeouts, leases, receipts, and risk classes.

These contracts are necessary but are not a substitute for the production
acceptance demonstration.

## Still open

The following remain explicit Stage 9 acceptance gates:

1. Pair or inspect a real installed device from the signed-in production
   website.
2. Send an authenticated proposed edit and command to that exact device.
3. Observe local approval, bounded output, final status, and the audit receipt.
4. Revoke and reconnect the device without exposing a reusable secret.
5. Visually prove online/offline, workspace, pending approval, last activity,
   rename, revoke, and reconnect consistently in both IDE and website.
6. Complete least-privilege GitHub, Vercel, Railway, Neon, email, and Telegram
   connection flows. Those provider integrations are not implied by the remote
   pairing tests.

Until those checks pass, Stage 9 is locally hardened but not production-E2E
complete.
