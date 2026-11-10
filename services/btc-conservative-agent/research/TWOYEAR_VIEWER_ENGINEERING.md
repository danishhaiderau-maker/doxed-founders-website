# Partial Two-Year Batch Study / Strategy Findings — draft engineering

## Pins and scope

- Engineering base: master `08c2b3762cc166bbc9dd91f45a13edb3a0cfd876`.
- AMEND6 SHA256 `65e8ea843da7dec58d7c2f748e280b90520ffb2c903e2edfb39b25dd7d749c66` (verified before use).
- Operative rules SHA256 `a9db190ee251c66f4dc8cbd7f608d6464678e7cdf1609230d2e6584070c933aa` (verified). This newer explicit binding supersedes the rules hash embedded in AMEND6; no obsolete W-era never-cheaper/LOSS-at-W or historical fee/funding/slippage deductions.
- RK AMEND6 design receipt `816714083f3fdc429d416f88b57772d57448ccd0884fa788cbf14aace92b9400`, DESIGN ONLY / conditional.
- Board Q1–Q8 `bfc65c6d0a65e28caaee8cb7d456306b83e8a3487869482c55d9e52eee08e16e`; HM design conditions `e20ba3a1e15ced976884588e19b17b8b40a9aa14c2f36be5de57e90fad11a1fb`.

This branch has NO producer, event writer, staging write, scheduler installation, chain runner or launch. The named chain card CHAIN-CARD-R60-TWOMONTH-WINDOWS-20261110-DRAFT.md remains PENDING. The default cache has no authority bindings, displays EMPTY, writer UNKNOWN, actual completion UNKNOWN, zero authorized displayed contributors. It cannot ingest real data automatically. No interim/manual JSON writer is supplied. Live OFF / PAPER ONLY is a configuration constraint, not an exchange observation.

The future supervisor-only producer must separately bind the exact final name `twoyear-summary-v2.json`, same-directory `.twoyear-summary-v2.json.staging`, flush/fsync/close plus os.replace, event receipts and leftovers under R21 or an approved recipe; otherwise STOP. Neither staging nor cleanup is implemented here. Producer tests/publication are a separate card.

## Baseline, changed files, imports and configuration

The dashboard at actual documentary v2c baseline `4978afd087c198e35efadaa9ec4b59b765fd0310` and engineering base are byte-identical: SHA256 `0baa9dfbf99db9d196177291de5b8e58cb342a3e8c9f49942e578eb55e19d798`. No #989 branch, deployment or inherited guard is assumed. No laptop was inspected or changed.

Changes under services/btc-conservative-agent:

- `research/research_dashboard.py`: five nav groups; all 26 old section IDs retained; three new sections; five study APIs plus read-only aliases; independent cache bypass of the old manifest-based cache; standalone guard, safe historical-ID shell and bounded listener. Embedded historical reporting remains otherwise unchanged. Standalone disables unsafe old routes, downloads and heavy template/AJAX paths; /data-health explicitly remains KNOWN GAP. No dependency on PR B.
- `research/twoyear_batch_view.py`: pure stdlib schema/immutable precompiled-byte store, HTML/API presentation and truthful empty/stale states. No producer/engine/NumPy import.
- `research/study_snapshot_loader.py`: stdlib external-supervisor adapter/child target; NOT imported or started by the viewer. No process-start or restart API. Child can read ONLY the supplied run-dashboard root's final snapshot, never staging or raw sources.
- `research/study_http_server.py`: stdlib bounded ThreadingHTTPServer transport; only Flask client metadata is prewarmed at construction. It starts no loader/producer/service.
- `test_twoyear_batch_view.py`, `test_study_http_transport.py`, `test_research_dashboard_navigation_groups.py`: deterministic fixtures and actual local socket/process tests.
- This engineering note. No added third-party dependencies, package or environment files.

Standalone invocation contract is `python -m research.research_dashboard --standalone --data-root <existing approved root>`; it is documented, NOT launched by this change. RESEARCH_DASHBOARD_BIND_HOST must be literal 127.0.0.1 and RESEARCH_DASHBOARD_PORT 9001 (existing defaults). A false/unknown FORCE_PAPER_MODE or live-enabled configuration refuses startup. The explicit root is startup-only; no file is opened by a request. Main starts only the bounded listener; absent an independently approved external supervisor, all study data stay empty. Embedded app integrations do not inherit the new listener's transport guarantees.

## Consumer contract and bounds (not a Windows acceptance claim)

Schema `twoyear-summary-v2`: source_as_of/generated_at retain source clocks; generation/series and full 64-character content hashes bind chain/freeze/rules/board/code/accounting/selection. Supervisor publication requires the complete immutable hash set; B2 requires a matching separately prebound H1-role release. Fixtures use synthetic hashes and cannot confer authority.

Ranked scope is at most B1–B5, B2 conditional; otherwise B1/B3/B4/B5. B6/B7 remain sealed and B8–B11 excluded. Every successful study API envelope includes explicit ranked IDs/count and n-of-11 scope wording. K40 defaults, 30/50 only slice the precomputed top-50. N_selection is 832 plus ALL recorded variants, separate from grammar181442. Per-batch statistics are displayed, absent components UNKNOWN, pooled method UNKNOWN. Ranking/order/tie scores are supplied OFFLINE, not calculated or statistically certified by this viewer.

Pin15 schema pin15-accounting-v1 freezes A-before-proven-filled-C-before-B-before-NORMAL semantics, capture=A+filled and filled=NORMAL+B+C, C included in filled, per-category shares, strict >5% flag and zero-denominator UNKNOWN. Missing adverse-extreme counts retain B/C membership and unknown win/P&L; an affected numeric ranking generation is refused, not partially improved. Validation is an aggregate consumer check, NOT the separately reviewed executable per-trade pricing/fill-evidence oracle.

Forward roles: (i) unchanged fill model DECIDES; (ii) same-signal/time gross mark RATIO ONLY. Signed slippage (ii)-(i), positive=cost, negative=improvement. Unknown/unmatched comparison is refused/UNKNOWN; negative fill-model P&L cannot become a viewer acceptance. No acceptance field is admitted. Exact low-power note and gross/no-depth banners accompany every forward context.

- Immutable precompiled HTML/API bytes; GET does no source open/hash/parse/IPC/wait/refresh or engine work.
- Cache TTL5s; source fresh<=60s, stale through3600s, future>5s UNKNOWN. TTL only selects a precompiled stale response. A loader failure precompiles a reason-bearing EMPTY fallback with original source metadata rather than serving attractive stale health numbers.
- Input/API524288 bytes, cap+1 read524289, chunks<=65536. Overflow rejects before parser. Total array records4096, depth12, strings2048 UTF8, strategies/Findings<=50, batches11, folds20, bins32, provenance128. Output API512KiB/HTML1MiB capped incremental sinks/chunks16KiB, no truncation or temp file.
- One GLOBAL try-lock/5s attempt slot across generations. The externally owned independent coordinator drops missed ticks; requests never call it. One supplied prestarted isolated child, watchdog100ms + join25ms, failure latch/no automatic replacement. `platform_verified=False` is the default HOLD. No Windows stop/reap proof is inferred from Linux tests.
- Admission BEFORE thread creation: seven normal slots, synchronous reject slot, backlog8. Header absolute50ms,8192bytes/32fields, line2048bytes; total complete-or-close250ms, socket slices<=50ms, sends<=16KiB. Overload response<=4KiB/10ms or close; no keepalive, pipelining or rejection thread. The watchdog closes sockets, never claims to cancel a Python handler.

## Acceptance coverage and outstanding gates

| AMEND6 test | Local author evidence / explicit limit |
|---|---|
| T1 | Cold/warm/empty/0/1/partial fixtures on HTML + five APIs; slow blocked child and cached routes. Windows not exercised. |
| T2 | 96 concurrent expired requests, global5s attempts and latch; real seven-handler admission plus synchronous rejection/backlog checks. |
| T3 | Real Linux blocked-child termination/reap assertions <=125ms total, stop<=25ms; actual slow header/drip/never-reading socket tests. Not Windows scheduling/handle proof. |
| T4 | Huge sparse file, continuously growing source, cap/cap+1, parser-denied overflow, duplicate/nonfinite/row/depth/string checks, TTL/source stale/future and no GET source work. Full peak-allocation instrumentation, cap-1/atomic-rename-on-Windows proof remain OPEN. |
| T5 | Banners, exact power/gross/sign notes, empty/populated/stale/error/Findings and unsupported methods. |
| T6 | K slicing/denominator/N invariance, contributor list, sealed/excluded/H1 rejection and unbound default. Offline ranking/tie engine not implemented/tested here. |
| T7 | Aggregate schema/reconciliation, no-fill wins, strict5%, zero denominator and missing-extreme refusal. Executable causal per-trade oracle and additive per-fold evidence remain separate pre-result gates. |
| T8 | +3 cost, -2 improvement, equality0, negative(i), mismatched/unknown and nonpositive reference fixtures. Real same-unit/signal-model provenance needs producer review. |
| T9 | HTTP spies deny file opens, JSON parses, sockets and process starts; symlink refusal and local binding/startup guards. Windows reparse/IPC/ownership/credential-deny import proof remains OPEN. |
| T10 | All old IDs, five groups, separate Findings, unsafe-download denial, explicit Data Health gap. |
| T11 | Exact baseline file comparison and changed import/config list above; Linux unit/socket tests only. Target Windows/Python transport/process/memory bounds remain HOLD. |
| T12 | OUT OF SCOPE: supervisor publication/writer is a separate card; no staged artifact or cleanup is created here. |
| T13 | Prebound hash and release mismatch reject, pending chain/writer UNKNOWN/default empty. Actual authority/oracle/producer/IPC pins and exact-head receipts remain UNKNOWN. |

These are fixture/author tests, NOT independent acceptance or a claim all T1–T13 passed. Runtime/real-data refresh and numeric rendering remain HOLD. Required: approved chain/producer/oracle/publication/IPC/loader bindings; Windows process stop/reap, memory/thread/handle and transport proof; PR A's own exact-head HM AGREE, RK PASS, Boss CLEAR, green CI/gitleaks; then separately approved OPS/read-back. PR B remains independent and supplies no proxy approval.

Broader Linux dashboard tests expose three existing failures: combos loader order, poisoned Windows OneDrive environment on Linux, and legacy JavaScript fabricated-zero checks. All three reproduce on an untouched archive of base master; this branch does not fix unrelated legacy panels.
