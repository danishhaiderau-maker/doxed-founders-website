# Verified local member reuse

Problem: every batch retry fetched all packages even after original-manifest
members had been durably copied to the laptop. Package scratch copies are
deliberately retired, so retaining another package cache is unnecessary.

Repair: the normal Python adapter passes the canonical local root to the
client. After current descriptor and original-manifest validation, every member
must match exact size and SHA-256 with stable file metadata and no linked paths.
Only a fully matching package skips payload requests. Missing/mismatched members
use normal bounded transfer; local evidence is not deleted by the reuse check.
The PowerShell parent independently checks exact destination, size, content and
strict boolean reuse status, saves the checkpoint and neither copies nor deletes
reused files. Original manifest ACK and terminal promotion remain unchanged.

Root verification: client + adapter + PowerShell suite: 68 passed in 27.92s.
384-file/three-package fixture: four metadata requests and zero payload requests
when local members match, versus seven requests for fresh transfer. This is a
fixture result, not measured production throughput. Independent review found a
parent size-validation gap; explicit Length comparison was added. The updated
PowerShell suite passed 9 tests in18.76s, including wrong-size refusal and a
preseeded reuse fixture that throws if promotion is invoked. These overlap the
earlier68 tests and must not be summed as independent cases.

Fly remains ab110c59fbf6 / PID664, bootstrap decision cursor27,284,929 at
2026-09-05T06:47:40Z, incomplete and no reported failure. Downloader remains
parked. This laptop repair requires no Fly deployment or index restart.
