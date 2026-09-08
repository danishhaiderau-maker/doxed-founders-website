# Terminal evidence renderer QA

Source under test: 3defda6. Date: 2026-09-09 (Sydney).

This is synthetic UI evidence, not market, profitability or production evidence.
The loopback-only `qa_shadow_tier_preview.py` injects the research-design response
using the actual projection helper and renders the actual dashboard HTML/JS.
It does not exercise publication freshness or transfer integrity.

## Verified in browser

- Empty-state desktop and mobile: missing-evidence text remains outside the hidden
  table. Computed table display is `none`.
- Populated desktop and 390x844 mobile: strict, conditional and delayed rows render
  independently with synthetic complete counts 2, 3, 5.
- Rightward mobile scroll exposes unknown counts 1, 4, UNKNOWN and venue acceptance
  NOT_LIVE_QUALIFICATION, UNKNOWN, UNKNOWN. Missing counts are not coerced to zero.
- Document clientWidth and scrollWidth are both 375 at the mobile viewport.
- The adversarial `<img src=x onerror=alert(1)>` timing value remains literal text;
  the evidence tbody contains zero image elements.
- Synthetic warning and disabled qualification remain visible. Desktop viewport
  was restored after the mobile check.

Production and the real analyzer on port 9001 were not modified. These checks do
not complete all dashboard navigation/export QA or prove a current analyzer cohort.
