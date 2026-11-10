# Data Health no-hang fix (draft engineering, no OPS acceptance)

Base: master 08c2b3762cc166bbc9dd91f45a13edb3a0cfd876.

The whole /data-health page is cached for 30 seconds, and each HTTP caller waits
at most 2.5 seconds for the one page refresh (including lock wait). Archive
insights use a 60-second cache with a 2-second caller deadline. Errors back off
for 5 seconds. These conservative defaults avoid rehashing 30 days on every
click while keeping recently completed report freshness visible. Each response
includes cached_as_of, age, stale/partial state, reason and refresh status.
Actual archive snapshot freshness is checked again on cached reads; verification
stays explicitly verify=True. Key inputs include root, days/cutoff, verification
and schema policy, retention inputs; page keys include all local report roots.

A daemon refresh occupies ONE slot per cache. Timed-out work is never replaced
until it returns; no executor shutdown wait, queued work or timeout-worker fanout.
Thus a permanently hung Python loader retains one slot and needs separately
approved process recovery; it is not claimed cancelled. The HTTP route returns
an honest partial/timeout 503 without stale health numbers. Insights preserves
its existing refusal contract, data=None for unavailable/stale components.

The Data Health render no longer contacts :9021. Other existing self-aware routes
are unchanged. There are no engine/collector starts, key reads, writes, live
controls or laptop actions in this change. Existing standalone binding and
transport are unchanged: this PR is not PR A's new server/loader contract.
Tests are Linux author tests, not Windows/laptop/runtime acceptance.
