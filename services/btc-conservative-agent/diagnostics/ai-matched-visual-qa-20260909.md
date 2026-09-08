# Matched AI section visual QA — partial

Source: 7d3d03f plus the synthetic preview fixture change. Browser tab 12,
http://127.0.0.1:9502/, desktop screenshot inspected on 2026-09-09.

- Synthetic banner and non-qualification caveats visible.
- Current matched comparison explicitly separated from historical calibration.
- Sample counts: independent episodes 3, supported rows 4, excluded 2.
- Long policy identifier stays inside a horizontally scrollable table.
- Historical empty state is explained, not represented as measured zero.
- Earlier legacy-only scope label was a defect; corrected in 7d3d03f.

This checks rendering with synthetic data, not current data publication,
profitability, all navigation paths, or full dashboard acceptance.
Mobile viewport and UNKNOWN visual state remain pending. The preview now supports
`--ai-state unknown`; compilation passed. In-app tab 12 disappeared before its
UNKNOWN screenshot could be captured, so no visual pass is asserted for that state.
