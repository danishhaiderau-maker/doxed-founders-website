# Attestation Dashboard — Step 5 of the Privacy Stack

> **Verify TEE inference and vault memory integrity — not trust-me badges.**

Step 5 closes the privacy stack loop: founders can **prove** Phala Copilot calls ran in a TEE and **audit** Founder Vault memory posture (encrypted relay, node online, vector index).

## What shipped

| Piece | Location |
|-------|----------|
| Phala receipt logging | `PrivacyAttestationLog` — every Copilot Phala inference |
| TEE verification | `POST /attestation/phala/verify` — Redpill attestation report + nonce |
| Live report | `GET /attestation/phala/report?model=` — fresh attestation fetch |
| Vault integrity scan | `POST /attestation/vault-scan` — memory mode + relay + node checks |
| Dashboard API | `GET /attestation/dashboard` |
| Builder UI | Settings → Builder → **Attestation dashboard (Step 5)** |

## Phala TEE verification flow

```text
Founder Copilot (Phala default)
       │
       ▼
Redpill chat/completions → request id + optional signing header
       │
       ▼
PrivacyAttestationLog (pending)
       │
       ▼ (Verify latest TEE response)
GET /v1/attestation/report?model=&nonce=&signing_address=
       │
       ├── signing_address present
       ├── intel_quote (Intel TDX) present
       ├── nonce_match (replay protection)
       └── optional GET /v1/signature/{requestId}
```

Structural checks pass when required fields are present. Full Intel TDX quote verification can be done via [Phala Cloud verify API](https://docs.phala.com/phala-cloud/attestation/chain-of-trust) or Redpill verifier SDK.

## Memory integrity score

| Check | Meaning |
|-------|---------|
| memory_mode | Founder Node or encrypted relay enabled |
| encrypted_relay | Vault blob on server is encrypted (zero-knowledge) |
| founder_node_online | Tray app heartbeating |
| vector_index | Local semantic index built (Step 4) |

Score = checks passed ÷ 4. **Scan vault integrity** records a snapshot in the attestation log.

## Setup

1. Complete Steps 1–4 (Founder Vault, BYO AI, Phala, Node v2)
2. Connect **Phala Private AI** and set as default Copilot provider
3. Ask Copilot a question — creates a Phala receipt
4. Open **Attestation dashboard** → **Verify latest TEE response**
5. Run **Scan vault integrity** when using Founder Node

## Privacy stack complete

1. Founder Vault ✅  
2. Bring Your Own AI ✅  
3. Phala Private AI ✅  
4. Founder Node v2 ✅  
5. **Attestation dashboard ✅**

## References

- Redpill attestation API: https://docs.redpill.ai/developers/api-reference/attestation
- Phala chain of trust: https://docs.phala.com/phala-cloud/attestation/chain-of-trust
- Step 3: `docs/PHALA_PRIVATE_AI.md`
- Step 4: `docs/FOUNDER_NODE_V2.md`
