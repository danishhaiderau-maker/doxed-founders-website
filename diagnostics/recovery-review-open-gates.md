# Recovery candidate review gates

Candidate is isolated and not deployment-ready. Root independently ran 60 passing tests with 2 native-symlink skips; those tests do not cover all crash boundaries.

## Proven source gaps requiring regressions and repairs

1. PREPARED append across deployment: committed-duplicate recovery excludes PREPARED. `_recover_append_head` compares original identity with current runtime identity and `_emergency_append` likewise checks current identity. Preserve the original hash-bound payload, validate it against the frozen obligation, and finish only against the matching ledger generation and EOF. Do not weaken ordinary append identity checks or repurpose the read-only identity override globally.
2. Close replay rereads `_paper_market_segment` from mutable tape. Frozen position/signal/outcome alone cannot guarantee identical derived write rows on retry. Freeze terminal write material before its first emission and retain it durably until acknowledgement.
3. Obligation capacity 128 is checked during protective close commit. A failed first FIFO item can starve subsequent items and eventually block closes. Reserve durable evidence capacity before new exposure, retain failed items with independent retry classification, and preserve a risk-reducing close path. Removing the cap without a storage bound is not an acceptable repair.
4. Verify live collector inputs after fill match frozen schedule and action timing fields, not only the two V3 write receipts.

## Review ownership

Root owns store recovery. fill_durable_evidence owns the PREPARED crash reproduction only. review_recovery owns capacity/starvation reproduction only. No production operations or whole-tree integration until these gates are closed.
