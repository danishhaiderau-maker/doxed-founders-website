# Authorized batch-upgrade park

User explicitly requested stopping the slow download before completing and testing
batch transport. Pre-stop read-only observation at 2026-09-05T01:44:57Z:

- Scheduled task: DoxxedFlyMirrorSync, Running; canonical loop script.
- Exact owner: PID10172, created 2026-09-05T01:08:30.451912Z,
  parent2052, pwsh.exe; only observed child16440 conhost.exe.
- Heartbeat: 01:44:56.9484366Z, file_start, index3295/34433, fileBytes0,
  remoteBytes732; receipt decision/2784c534c5832aa3f27b711279f4f9e29f9a3e7109b24bf619a954d54e328b66.json.
- Observed/deployed df45887e1526; retained mirror9b588c0b5f79; MISMATCH.
- Checkpoint: 804735 bytes, modified01:44:50Z. No terminal sync/ACK receipt.

Planned authorized operation: disable this named task, stop its existing instance,
then verify exact owner absence. Preserve downloaded evidence, checkpoint and
partial candidates. Do not delete source data or restart until reviewed batching
integration and integrity/pressure tests are ready. No Fly restart/deploy or
trading control change is part of this park.

Post-stop verification at 2026-09-05T01:45:33.7896284Z: task Disabled,
Settings.Enabled=false, PID10172 absent, zero canonical script owners. The final
retained heartbeat is 01:45:18.9318161Z, file_start3322/34433. Its inProgress=true
is historical, not a live-process claim. Checkpoint remains valid JSON with3334
entries (includes prior checkpoints, not3334 new files this run), 812623 bytes,
SHA256 67D4DF3FF2E120C62A72B0E35206FC8089FC841C24296E3C2A770C4DA79A2F2F.
No data, partial candidate, source evidence or checkpoint was deleted.
