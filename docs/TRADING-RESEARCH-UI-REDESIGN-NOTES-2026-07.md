# Trading and Research UI follow-up

This note keeps the showcase dashboard (`:7002`), research analyzer (`:9001`), Agent Hub, and Founder IDE aligned to one interaction system. It is deliberately separate from the executor/watchdog release so visual changes cannot obscure production-safety review.

## Shared product structure

- **Overview** — current health, exposure, P&L, freshness, and the safest next action.
- **Work / Decisions** — AI calls, lane decisions, orders, positions, outcomes, and review actions.
- **Connect / Data** — source ownership, relay state, analyzer cohorts, exports, integrations, and audit evidence.
- Keep advanced configuration and raw logs behind a selected entity or a clearly labelled detail panel.

## Visual contract

- Use native system/Inter-like typography: 13–14 px body, 11–12 px metadata, and 18–24 px page headings.
- Use a 4/8/12/16 px spacing rhythm, 28–36 px controls, and 4–8 px radii.
- Avoid nested cards, oversized hero copy, decorative all-caps text, and pill buttons that are not true states or filters.
- Reserve blue for the primary action/selection. Green means verified healthy or realized success; amber means pending/degraded/stale; red means blocked/failed/destructive/live risk; gray means unknown/offline/inactive.
- Pair every important status with a word/icon, evidence timestamp, source, freshness, and reason.
- Use restrained 120–180 ms transitions with stable dimensions and no decorative motion.
- Financial/destructive actions require an impact preview, explicit confirmation, and durable receipt.

## Responsive contract

- Keep the primary navigation stable; collapse secondary detail panes before the main state/action area.
- Convert wide tables into compact entity rows with a detail sheet on narrow screens.
- Truncate long labels with a tooltip; preserve stable widths for status and actions.
- Remote/mobile views prioritize status, approve/stop/review, exposure, and freshness.

## Truth and terminology already fixed

- `AI Call Time` is now separate from `Lane Recorded Time`, with one shared paid-call ID threaded into downstream lane rows.
- Tile cards distinguish **Executed**, **Open/Pending**, and **Paused Shadow** results.
- Analyzer cohorts distinguish **Fresh Collection**, **Historical deduplicated executed trades**, and **Paused Shadow**; they are never silently summed.
- Paused Shadow is labelled `NEVER_RELAY_ELIGIBLE` and remains outside global paper/exchange books.
- Analyzer run state distinguishes `RUNNING` from `IDLE_BETWEEN_RUNS`.

## Follow-up cleanup candidates

Remove only after route/usage tests prove the replacement:

- duplicate owner/connected/online indicators that report the same evidence;
- ambiguous `queued`, `success`, or green states without acknowledgement evidence;
- retired tile/version controls from the active surface (retain archived data readers);
- implementation-oriented error text and raw HTML/JSON from normal user flows;
- repeated download/export links that can become one contextual Export action;
- wide historical tables when a row/detail interaction communicates the same information;
- configuration controls from the overview when they belong in advanced details.

Retain:

- fail-closed pause/stop controls, exposure and reconciliation evidence;
- trade/lane provenance, timestamps, call IDs, order IDs, and audit exports;
- one-click stack start/stop sequencing;
- explicit separation between exchange net position and virtual tracked lots.

## Delivery order

1. Inventory the existing :7002/:9001/Agent Hub surfaces and map every control to Overview, Decisions, or Data.
2. Build shared tokens/components for status, timestamps, metrics, empty states, confirmations, and detail sheets.
3. Redesign :7002 without changing strategy or relay behavior; verify desktop and narrow layouts.
4. Apply the same components to :9001 and Agent Hub; remove duplicate navigation and status evidence.
5. Run visual regression, keyboard, responsive, accessibility, and financial-action confirmation tests before deleting legacy rendering code.

