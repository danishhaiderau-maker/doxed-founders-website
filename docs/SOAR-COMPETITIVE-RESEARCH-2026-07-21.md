# SOAR competitive research

| Field | Value |
| --- | --- |
| Reviewed | 2026-07-21 |
| Product | SOAR web application, public documentation, company profiles, and legal terms |
| Verdict | Valuable disclosure and discovery patterns; not a safer or more complete Founder OS |

## What SOAR is doing

SOAR positions itself as an on-chain startup capital-formation and discovery platform. Founders create a company profile and launch an "Ownership Coin". Participants discover companies, inspect supplied materials and market data, and contribute using the platform flow.

Its documented DRP model currently says:

- a fixed maximum supply, commonly one billion tokens, represents the company's future value;
- roughly 5-7% is initially issued to a bonding curve and liquidity, while most supply remains unminted;
- future issuance is frozen for three months;
- after the freeze, a founder can publish a token request, wait 72 hours, and mint more supply;
- circulating supply creates a matching senior-debt obligation between the company and SOAR;
- a buyback reduces that obligation;
- an acquisition, IPO, or other agreed liquidity event must settle that obligation;
- SOAR reserves discretion over how received settlement value is ultimately used.

SOAR also uses a three-level trust ladder:

1. **Project:** basic same-domain email verification.
2. **Verified:** identity/business checks, domain TXT verification, and signed launch/debt agreements.
3. **Curated:** more than 30 days of trading plus deeper SOAR research, verified business details, and demonstrated execution.

The current product surface is intentionally simple: category discovery, company cards with FDV and sector, company pages with Overview, Chart, Community, Team, supplied artifacts, Contribute, and Share actions, plus an AI Autopilot for discovery and market questions.

## What SOAR gets right

- **Trust is progressive and visible.** Identity, domain control, legal agreements, business review, and curation are not collapsed into one vague checkmark.
- **Company pages are the product.** Team, documents, community, market activity, and participation live together instead of sending users to several external analytics sites.
- **Dilution is announced.** A public request and delay before future issuance is much better than an undisclosed team mint.
- **The acquisition case is designed up front.** SOAR treats an exit as a core lifecycle event rather than improvising after a company receives an offer.
- **The navigation is easy to scan.** Discover, category filters, a company profile, and a single contribution action are clearer than a dashboard full of unrelated controls.

## Material weaknesses and contradictions

These are reasons to learn from SOAR, not copy it.

- The detailed DRP page calls the token neither debt nor equity and says it has no inherent rights, while other pages market it as an ownership-like claim and describe a senior-debt settlement. Those statements require specialist legal reconciliation; a disclaimer does not neutralize the economic substance.
- One page says 95% remains unminted and may later be requested, while the glossary says FDV equals market cap because total supply equals circulating supply. Those supply definitions are not presented consistently.
- The detailed model describes a wallet controlled by the founder and SOAR, while the terms call the platform self-custodial and say SOAR does not control wallets or transactions.
- Settlement proceeds are payable to SOAR and their use is discretionary. That is less direct and less predictable for holders than a pre-funded, rule-bound settlement vault with explicit claimant rights.
- A 72-hour notice after only three months still permits a very large founder-selected mint. It is more flexible, but creates more dilution discretion than Founder's continuous ten-year stewardship schedule.
- Company artifacts on the reviewed profile were visibly labelled "Not Scanned". Founder must malware-scan, hash, provenance-label, and permission-gate every uploaded diligence file.
- The terms describe SOAR as passive and not an exchange, custodian, broker, or settlement provider even though the product and documentation use trading, contribution, wallet, bonding-curve, and liquidity-event language. Founder must settle the regulatory model before launch, not rely on labels.

## What Founder should adopt

### 1. A stronger verification ladder

Use four precise states rather than one badge:

| State | Evidence |
| --- | --- |
| Listed | Same-domain email and project ownership claim |
| Founder Verified | Person identity, sanctions screening, wallet proof, and hardware-backed account |
| Company Verified | Entity, domain TXT, beneficial ownership, jurisdiction, and signed disclosures |
| Founder Reviewed | Product evidence, operating history, financial/disclosure cadence, security review, and human committee decision |

Every badge must open an evidence sheet showing what was checked, by whom, when it expires, and what was not checked. "Reviewed" is not an endorsement or price prediction.

### 2. Public stewardship receipts

Every vest, beneficiary rotation, treasury proposal, reserve release, liquidity change, emergency action, and settlement deposit should produce a public, hash-chained receipt. Material changes get a disclosed delay and holder-visible notification. Founder Enterprise keeps the continuous ten-year vest rather than allowing an arbitrary post-freeze mint.

### 3. One complete project profile

Build Overview, Build activity, Team, Verified evidence, Documents, Market, Holders, Vesting, Community, Updates, Acquisition interest, and Settlement in one profile. DCF-owned indexed events remain canonical; DEX Screener/Birdeye data is enriched and timestamped, never treated as settlement truth.

### 4. Acquisition interest and settlement

Borrow the idea that an acquisition is part of the product lifecycle, but improve the mechanism:

- offers are non-binding until founder/company/legal approval;
- the live data room shows circulating, founder-vested, founder-locked, LP, treasury, and holder-cohort supply;
- transaction documents define whether token holders have any proceeds right;
- when they do, the acquirer/company fully funds an immutable settlement vault before claims open;
- eligible holders burn or surrender tokens and claim stablecoin directly under a published formula;
- SOAR-style platform discretion over settlement proceeds is not used.

### 5. Founder AI as operating copilot

Add discovery and diligence questions to Founder AI, but do not present "good trade opportunities" as an AI promise. Founder AI should explain verified evidence, changes, risks, supply schedules, runway, build activity, and source timestamps. Recommendations involving financial products require the approved regulated path.

## Recommended Founder model

SOAR validates the core insight that a founder launch should resemble a long-lived company lifecycle, not a one-day meme launch. Founder should remain differentiated:

- **Founder IDE + embedded Node:** build, operate, deploy, monitor, and remotely control the actual company.
- **Founder identity and evidence:** progressive verification tied to real work and disclosures.
- **Founder Enterprise launch:** 50% Founder Stewardship Vault vesting continuously over ten years, 40% bonding curve, and 10% disclosed graduation liquidity reserve, subject to legal classification and audited contracts.
- **Fair Launch:** a separate lower-insider-allocation template for communities that do not want the enterprise model.
- **DCF Swap:** founder-aligned post-graduation fee, explicit custody, proposal/approval controls, and no automatic dumping.
- **Acquisition workflow:** qualified interest, controlled data room, negotiated transaction, funded settlement vault, and direct auditable claims when legally granted.

## Sources

- https://app.launchonsoar.com/?category=curated-projects
- https://docs.launchonsoar.com/overview/icm
- https://docs.launchonsoar.com/overview/curated-verified-unverified
- https://docs.launchonsoar.com/drp/how-it-works/detailed
- https://docs.launchonsoar.com/drp/how-it-works/simple
- https://docs.launchonsoar.com/drp/how-its-different
- https://docs.launchonsoar.com/for-participants/participate
- https://docs.launchonsoar.com/legal/terms-and-conditions
