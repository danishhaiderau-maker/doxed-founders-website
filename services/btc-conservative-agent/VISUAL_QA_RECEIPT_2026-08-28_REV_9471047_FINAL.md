# Visual QA Receipt — Analyzer Hypothesis Paper Tiles

- Tested: 2026-08-28 19:38–19:45 AEST
- Production: https://doxed-btc-bot.fly.dev/
- Analyzer: http://127.0.0.1:9001/
- Production revision: `9471047f2716`
- Production version: `v31-five-family-analyzer-hypothesis-paper`
- Fresh epoch: `epoch-4661cc4ae5c1cb6648b82685`
- Tile registry signature: `87ec52b3df04d50580e8fcd632de2a5996f253c0ae55bbe5be2eb12945dabafd`
- Viewports: desktop; narrow/mobile 390 x 844

## Production routes and expectations

| Route / area | Expected | Actual | Result |
| --- | --- | --- | --- |
| Overview / header | Exact deployed revision, version, epoch and PAPER ONLY boundary | Exact values rendered; Bitfinex relay disarmed | PASS |
| Decisions / Pathway Lab | Exactly five replacement hypothesis tiles with exact policy parameters | Five tiles rendered and ON for paper eligibility | PASS |
| Chandelier | 0.30% offset, windows 2/3/4, 180s, 50% gap, 1.5 ATR | Exact values and policy identity rendered | PASS |
| Fixed Target | 0.27% offset, windows 2/3/4, 180s, 50% gap, TP 2.5 + Scenario C | Exact values and policy identity rendered | PASS |
| ATR Trail | 0.30% offset, windows 2/3/4, 180s, 50% gap, SL 1.5 / arm 0.75 / trail 1 | Exact values and policy identity rendered | PASS |
| Hybrid Runner | 0.30% offset, windows 2/3/4, 180s, 50% gap, secure 25% + 25% runner | Exact values and policy identity rendered | PASS |
| MFE Giveback | 0.30% offset, windows 2/3/4, 180s, 50% gap, retain 80% MFE | Exact values and policy identity rendered | PASS |
| Data / activity tables | Seven downstream tables usable on narrow viewport | All seven are labelled focusable horizontal-scroll regions; no page-wide overflow | PASS |
| Hypothesis evidence | Historical diagnostic receipt visibly separated from prospective paper execution | Separation copy and all five receipts visible | PASS |

## Analyzer routes and expectations

| Area | Expected | Actual | Result |
| --- | --- | --- | --- |
| Deployed policies | Exact five production policy identities available during an empty current epoch | Five IDs and hashes shown under `DEPLOYED POLICIES COLLECTING` | PASS |
| Policy epoch | Current replacement contract visible | `v31-analyzer-hypothesis-paper-v1` rendered | PASS |
| Empty evidence | No fabricated qualification or execution metrics | Empty current cohort labelled collecting / insufficient evidence | PASS |
| Narrow layout | Long policy and report text wraps without clipping | No non-PRE page overflow; PRE content wraps within 365 px | PASS |

## Runtime observation

- Two post-deployment AI assessments completed by 19:40 AEST.
- Both were `NO_TRADE` assessments and created zero pending orders and zero positions.
- WebSocket was ready and fresh at observation time.
- PAPER_ONLY collection was running; live relay and Bitfinex execution were off.

## Screenshot and retest record

- Desktop production screenshot captured in the Codex in-app browser at the Pathway Lab. It shows the replacement Chandelier and Fixed Target tiles, exact timing parameters, paper-only boundaries, current-period counters, and analyzer hypothesis receipts.
- True 390 x 844 production retest passed with no page-wide overflow and usable horizontal table scrolling.
- True 390 x 844 analyzer retest passed with wrapped policy/report content.
- Final defects: none found.

