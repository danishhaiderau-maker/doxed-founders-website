# Current-lane evidence separation visual QA

- Checked: 2026-08-26 06:14 AEST
- Local candidate: `66e187d0bc25` plus the lane-evidence working-tree repair
- Fly source revision represented by the report: `26db6ab2f786881d64ec1d977f2edcae7fa6787e`
- Epoch: `epoch-5c7b70f8d7e64985a67b6403`
- Tile registry signature: `43b1cff08a1c6ea75f1c84ad8a99772afb880ce2413f3c7d5d1306b76c377b2f`
- Route: `http://127.0.0.1:9001/` -> Lanes & AI -> Current Lanes

## Result

PASS for the affected current-lane view.

- Desktop rendered table separates executed closes/PnL/EV from counterfactual
  terminals/PnL.
- API and rendered values agree:
  - `OFFSET_029_ATR_TP_25`: 2 executed closes, +$0.21 executed PnL,
    0 counterfactual terminals.
  - `CONTINUOUS`: 0 executed closes, $0.00 executed PnL,
    51 counterfactual terminals, -$0.01 counterfactual PnL at the checked
    analyzer generation.
- The visible explanatory note states that counterfactual results never count
  as fills, executed PnL, or strategy qualification.
- Mobile viewport 390 x 844 had no document-level horizontal overflow. The
  wide evidence table remains contained in its scrollable table viewport.
- No browser console warning or error was observed on the affected view.

This is an affected-view receipt, not a global dashboard or system readiness
claim. Full navigation and production control QA remain separate gates.
