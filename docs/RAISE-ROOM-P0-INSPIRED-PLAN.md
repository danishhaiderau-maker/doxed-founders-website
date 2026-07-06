# Raise Room P0 — Product discovery hub (inspired plan)

**Status:** Phase 1 discovery hub **shipped** (Jul 2026). DDollar escrow commits remain Phase 2.

## Shipped (this recovery)

- `GET /raise-room/dashboard` — hero stats, trending, activity feed, leaderboards
- `GET /raise-room/projects?filter=` — trending, newest, high conviction, AI picks, etc.
- `/raise-room` discovery hub UI — hero, filters, cards, sidebar, demo empty state

## Still missing (Raise Room agent / Phase 2)

1. **Schema** — `TokenLaunch`, `LaunchInterestCommit` for DDollar escrow commits
2. **API** — commit/cancel DDollar escrow, founder snapshot export
3. **Points** — `LAUNCH_WHITELIST_COMMIT` / `REFUND` ledger types
4. **UI** — tier picker, launch window card, DDollar commit heatmap beside paper demand
5. **Copy/legal** — escrow disclaimer on project + Raise Room surfaces
6. **Phase 2** — Merkle proof pages, on-chain claim

## Safe starting points

- `SimulatedRaise`, `raise-room-panel.tsx`, `exportRaiseParticipants`
- List Project + Trust Center + Phase 1.5 trust gates

See `docs/PLATFORM-REQUEST-AUDIT.md` for full checklist.
