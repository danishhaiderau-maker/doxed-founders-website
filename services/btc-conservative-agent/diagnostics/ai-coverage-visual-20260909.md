# AI coverage visual QA — partial

Candidate: 2ed1cd8 plus preview-only fixture changes.

An isolated loopback synthetic preview on port 9513 was inspected at 1265 x 712.
Historical Research -> AI comparison & calibration navigation worked. The visible
coverage text showed APPROVE, REJECT, NO_TRADE, AI_NOT_CALLED, ERROR and UNKNOWN;
the missing ERROR value rendered unavailable. Research-row and independent-trade
distinction was visible. No overlap was seen in the inspected viewport.

Limitations: the complete row/table extended below the inspected viewport;
full-page and mobile verification are outstanding. This synthetic preview does
not establish current analyzer publication, market outcomes or qualification.
The temporary browser tab was closed and preview session stopped; port 9513 had
no listener on the subsequent check. Existing 9001/9502 services were untouched.

Follow-up runtime check: temporary DATA_DIR was rejected by the production
canonical-path import guard. Restored the required import path without weakening
the guard; the fixture replaces DATA_ROOT/ROOT immediately after import. This
does not prove import-time filesystem isolation. UNKNOWN-mode HTTP check passed
with no matched groups and an explicit synthetic missing-evidence blocker.
