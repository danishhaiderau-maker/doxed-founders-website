# Capture and inventory observation

Source: live public `/api/status` and authenticated paged manifest on canonical Fly owner, 2026-09-09 around 08:07 AEST. Revision `bdfcd7372057`.

Compared with the earlier same-process status sample:

| Counter | Earlier | Latest | Delta |
| --- | ---: | ---: | ---: |
| Microstructure rows written | 2456 | 2752 | 296 |
| Missed observation buckets | 429 | 461 | 32 |
| Write failures | 0 | 0 | 0 |

Collection is advancing, but missing observations continue. The recorded share for these two counters over this interval is 296/328, approximately 90.24%; this is not a completeness guarantee for all research streams, nor two days of uninterrupted operation. No missing buckets are backfilled.

The public runtime does not yet expose the candidate's append/quote-lock/trade-lock phase timings. This sample cannot attribute gaps to disk append, CPU scheduling, or lock contention. Preserve the measured timing repair for a reviewed deployment; do not change capture queue behavior from these aggregate counters alone.

Inventory remains the same live single-flight worker and resume token: SCAN, 13103 files inspected, 101 invocations, 11 pending directories, no failure. SHA and ACK eligibility are absent. Inspected files are not transferred files. No restart or download was initiated for this observation.

Next: complete inventory and verified transfer without disruption; then current analyzer publication. Capture timing diagnosis remains separately open.
