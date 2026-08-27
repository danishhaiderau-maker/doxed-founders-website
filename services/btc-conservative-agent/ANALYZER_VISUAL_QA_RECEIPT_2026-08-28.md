# Analyzer Visual QA Receipt — 2026-08-28

- QA time: 2026-08-28 00:29–00:35 AEST
- Surface: `http://127.0.0.1:9001/`
- Dashboard version: `v31-five-family-atomic-paper`
- Dashboard code HEAD: `36be5ffd2a2f17b2edf1afa7c53f201a9e158a21`
- Published analyzer generation revision: `eae441b81eea288f69077d5ed44e916e642ee52b`
- Generation ID: `67a19bbc48ac3dafcc70729a`
- Epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Source data revision: `901fbbf99e8188972ff0e5512f542e259200ad6279fe0cb7d008dbf95656f52c`
- Browser: Codex in-app Chromium browser
- Viewports: 1440 × 1000 desktop; 390 × 844 narrow/mobile

## Routes and controls tested

Every visible analyzer section was opened first-hand at the mobile viewport:

- Overview: Overview, Findings, Regime & ADX.
- Lanes & AI: Current Lanes, Direction & Gap.
- Chase & Exits: Attribution, Chase Policy Lab, Threshold, Delay, Top 100 Policy Combos, Legacy Gap Performance, Exit Combos, Exit Reason Leak, Ladder Simulator, Historical Exit Leakage.
- Genome & Reports: Safe Policy Genome V3.1, Edge & Features, Report Explorer, Archives, Downloads, Pathway Audit, Historical Recovery.

All 60 Report Explorer buttons were clicked in four bounded batches. Every control loaded non-placeholder JSON, selected state exposed `aria-current="true"`, and no load-error response was observed.

## Responsive and accessibility checks

- Desktop body width stayed within the 1440 px viewport.
- Mobile body width stayed within the 390 px viewport on all 22 sections.
- All visible tables on every section were wrapped by a focusable `role="region"`, `tabindex="0"` container.
- Top-policy, exit-combination, archive, leakage, audit, and recovery tables no longer force page-wide horizontal overflow.
- Exit-combination table containers measured 351 px viewport width against 1,036 px and 1,306 px content widths with `overflow-x: auto`.
- The archive table measured 351 px against 664 px and was directly scrolled from `scrollLeft=0` to `scrollLeft=280` (maximum 313), proving that hidden columns and ZIP links are reachable.
- Archive ZIP controls were rendered and visible inside the scrollable table.
- Report Explorer entries are native buttons and keyboard-focusable.
- Diagnostic, paper-executed, and shadow/lab evidence labels remained visibly separated on the inspected views.
- Empty current-generation sections displayed explicit insufficiency/no-terminal-evidence language rather than fabricated performance.

## API/render parity checks

- `/api/status` reports 60 reports, five active tile signatures, four separately scoped observed-execution signatures, matching epoch/revision, and an explicit dashboard restart receipt.
- `/api/archives` uses the archived manifest timestamp as authoritative and exposes the older summary timestamp separately.
- `/api/features`, `/api/leakage`, `/api/spread-performance`, and `/api/horizon` expose generation identity plus an explicit insufficiency reason when empty.

## Screenshots

- `diagnostics/visual-qa/2026-08-28-analyzer/desktop-report-explorer.png`
- `diagnostics/visual-qa/2026-08-28-analyzer/mobile-archives.png`
- `diagnostics/visual-qa/2026-08-28-analyzer/mobile-exit-tables.png`

## Result and limitations

Retest status: PASS for the repaired responsive-table and Report Explorer accessibility defects on the currently served analyzer dashboard.

This receipt does not qualify a trading policy and does not claim that empty evidence has matured. A later scheduled analyzer generation, completed mirror handoff/defer cycle, production deployment of these local analyzer repairs, and production-dashboard post-deployment visual QA remain separate gates.
