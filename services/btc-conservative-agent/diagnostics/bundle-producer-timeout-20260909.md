# Pinned bundle producer timeout

Authenticated index and Fly-volume JSON were read on 2026-09-09. Generation:
`a9eaa1341ddbe111af7c5be8949ff72eed7f440d4db80eba8a6d72dc284a96df`.

- Published packages7, members261, payload54121041 bytes. Laptop receipt matches.
- Producer last-attempt terminal FAILED, BUNDLE_CIRCUIT_OPEN, last error
  BUNDLE_SLICE_TIMEOUT. Recorded 2026-09-08T18:25:04.628793+00:00.
- Durable cursor page2/row75/index_offset386; adaptive_member_limit64;
  skipped INELIGIBLE_PATH_PER_FILE_FALLBACK192; completed=false.
- This is last-attempt evidence, not a claim about all current processes.
- Fly SSH printed valid matching state JSON, then its Windows client returned
  'The handle is invalid'; command exit was1, not claimed successful execution.
- Recent bounded Fly logs contained no matching bundle/OOM messages.

Source diagnosis: run_slice's12-second subprocess timeout covers import, storage
admission and worker. The worker5-second cooperative deadline begins later.
The lease is nonblocking; existing packages are stat-enumerated, not rehashed.
No phase receipt currently distinguishes admission from read/build/fsync timeout.
Do not claim any of these phases is proven responsible or raise timeouts blindly.

Next: add nonce/identity-bound bounded timeout phase observation with tests, then
use reviewed guarded recovery. Current downloader remains its sole live owner;
no raw deletion, restart, mirror promotion, ACK or live arming performed here.
