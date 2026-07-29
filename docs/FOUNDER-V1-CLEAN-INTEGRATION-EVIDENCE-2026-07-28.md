# Founder V1 Clean Integration Evidence

Date: 2026-07-29

This record describes the local Founder V1 integration candidate. It is not a
production deployment receipt and it does not claim that the installed,
clean-machine, signing, remote-production, or soak gates are complete.

## Candidate identity

- Branch: `release/founder-v1-clean-integration`
- Source candidate tested before this evidence update:
  `55ac43f8` (`fix(founder-ide): expose essential founder navigation`)
- Production base: `c7f655da0709407b6f378a5c00617d3c170adb6a`
- Required safety ancestor `06c7d02e408fddb7875a741ebcc8a6415ca9a797`:
  present
- Founder commits after the production base: 84
- Current local `origin/master`: `c7f655da0709407b6f378a5c00617d3c170adb6a`
- Relationship at tested product commit: 86 ahead, 0 behind
- Changed files in the Founder delta: 354
- Protected trading/analyzer/workflow paths in the Founder delta: 0
- Worktree status at evidence capture: clean
- Conflict markers: none
- `git diff --check`: pass

The Founder line was rebased onto the current production base using a protected
backup reference. The final delta remains free of trading, analyzer, exchange,
and production-gate paths. Subsequent Founder-only commits refreshed the
reviewed extension bundle, corrected the device preview, and made the essential
Deploy, Remote, and Connect destinations reachable in Founder mode. No push,
master merge, production deployment, or relay-state change occurred.

## Automated evidence

The following checks passed on this clean integration candidate. The final
Dragon commit changes only the desktop companion HTML and its focused test; the
unrelated full-suite evidence below was gathered on the same integration line
before that two-file visual change.

| Surface | Evidence |
| --- | --- |
| API | 34 suites, 309 tests passed |
| API TypeScript | no-output compile passed |
| API emit compile | passed |
| Web | Next.js 15.5.18 production build passed; 58 static pages generated; build ID `00e4_EQE0YrK5Edq1fGHV` |
| Founder IDE extension | 52 suites, 213 tests passed |
| Founder Node | 22 suites, 62 tests passed |
| Founder Node TypeScript | build passed |
| Shared utils | 99 tests passed |
| Founder release manifest | 18 tests passed |
| Release evidence contract | 8 tests passed |
| Fast development workflow | 2 tests passed |
| Overlay and installer contracts | 40 tests passed |
| Provider profiles, native completion, Skills, and Second Brain | 25 tests passed |
| Website shell predicate | 6 tests passed |
| Website remote label | 3 tests passed |

The clean extension output was generated before the Founder Node suite because
the Node relay protocol imports the compiled extension protocol. Generated
extension output changes were restored after verification and were not included
in the Dragon commit.

The final production-descended candidate was also checked with the 25-test
Founder mode/provider/Second Brain contract group and the 38-test smoke group.
The smoke assertions all passed; after test completion the Windows Node process
printed a libuv handle-closing assertion while shutting down. This is recorded
as QA-runner shutdown noise rather than hidden or misreported as a product test
failure.

The final website build was started locally from its optimized output. A GET to
`/founder-id/authorize` returned HTTP 200 with 10,295 response bytes and no
server process remained afterward.

## Secure device pairing evidence

The production-descended candidate adds a founder-readable approval preview
before a laptop can join the account:

- the anonymous device-code create route accepts only bounded device metadata
- the authenticated inspect route atomically claims the request to the signed-in
  founder and returns a label, platform/version, expiry, and 12-character
  install fingerprint
- authorization can approve only the already inspected readable code; browser
  input cannot choose a node id, secret, or device metadata
- Founder Node derives the same fingerprint locally from its install identity
  while keeping the raw install id, IPC secret, and device code out of the
  renderer
- API pairing coverage and the full API suite pass; Founder Node is 62/62
- the built web route was visually inspected at 1280 by 720 in missing-code and
  readable-code states with no overflow or collapsed content

The local production server reports a NextAuth client configuration warning
because production authentication secrets are intentionally absent from this
isolated build. No sign-in was attempted and this warning is not presented as
authenticated end-to-end proof.

## Living Dragon visual evidence

Playwright rendered the actual transparent companion surface at 360 by 300 CSS
pixels with a device scale factor of 2.

- States inspected: idle, working, coordinating, success, attention
- Images loaded: yes
- Horizontal overflow: none
- Vertical overflow: none
- Transparent page background: preserved
- Idle motion: two frames 900 ms apart changed 79,106 of 432,000 pixels
  (18.31 percent), proving that the resting state is animated
- A disconnected artifact in the idle source image was detected by visual QA
  and masked without changing the Dragon or nest artwork
- Reduced-motion rules disable the new state animations
- Focused companion tests: 2 passed
- Full Founder Node suite after dependency build: 60 passed

Temporary local render evidence:
`%TEMP%\founder-dragon-qa-20260728\`

## Installed application evidence

The internal QA installer built from the current Founder product source is:

- Path:
  `C:\Users\user\Desktop\Final Bots\worktrees\founder-v1-clean-integration\packages\founder-ide\installer\dist\Founder-IDE-Setup-0.9.4-internal-qa.exe`
- Size: 323,910,432 bytes
- SHA-256:
  `6CCAFEED6211544E43DFABE95CA18901EEA18F7DB72AE94153AB0FD8678AE48F`
- Signature: unsigned, correctly labelled internal QA rather than production
- Installer log:
  `%TEMP%\Founder-IDE-0.9.4-0.5.2-final-install.log`

The installer log records inner installer exit code 0, workbench/style/
integrity/navigation corrections, shortcut creation, release identity 0.9.4,
and normal completion without a Windows restart. The installed extension is
0.5.2 and `founder-release.json` reports 0.9.4.

The installed app was launched with an isolated debugging port and audited
through its native Electron surfaces. The final run proved:

- the Founder control center opened without critical console or page errors
- Founder branding was visible and `Void's Settings` was absent
- New chat, Projects, Chats, Agents, Browser, Changes, Deploy, Remote, and
  Connect were visible
- the narrow Goal panel kept `Housekeeping` and `Edit` readable and clickable;
  direct geometry evidence proved both buttons stayed inside the 128-pixel
  action row without overlap
- the native composer exposed Founder Auto, Second Brain, screenshot
  attachment, model controls, and the microphone control
- a real PNG produced a local preview, could be removed, and left no critical
  console or page errors
- Projects opened with search and open-folder controls
- Chats returned to the native composer with Second Brain, attachment, and
  microphone controls
- Founder Settings showed Founder Free, the 200K weighted-token weekly
  allowance, Plan and Usage, BYOK, and Identity and Node
- Deploy opened the installed service-connection surface
- Remote opened the installed in-app remote-control surface
- Connect opened Founder Settings with GitHub, Vercel, Railway, and Neon
- a fresh explicit `founder-os-fast` chat completed visibly through managed
  DeepSeek V4 Flash in 3,616 ms total, with provider latency 798 ms, a truthful
  `fast / deepseek-v4-flash` receipt, no visible gateway error, and no critical
  console or page errors
- the installed terminal created a real PowerShell process and wrote the
  unique marker `FOUNDER_TERMINAL_V1_20260729_1310` to the evidence directory,
  proving the packaged native terminal dependency works

The navigation harness rejects stale/background webviews and requires the
Founder Home view before clicking a destination. The chat harness can start a
new chat across the Founder webview boundary, select a visible route and work
mode, and evaluates only the new response rather than accepting an older route
receipt from scrollback.

A fresh `founder-os-auto` request was also run after starting a new chat. The
request completed successfully, but the deployed API returned
`reasoning / deepseek-v4-pro` instead of the V1 `fast /
deepseek-v4-flash` default. The source candidate already contains commit
`4108904d` and passing tests that force routine Founder Auto and legacy fallback
to Flash. This is therefore recorded as an undeployed production-policy gap,
not hidden as an installed-client failure and not claimed complete.

The installed Plan-mode selector is visible and selectable. Its managed request
failed three times with the safe generic Founder Connections error and no
provider response. The current source candidate includes the later Founder
Agent tool/history pass-through work, while the deployed API used by this
installed test does not yet provide the matching production contract. Build,
Debug, and Team are therefore not being marked complete from source-only tests.

An Extension Development Host was not used to manufacture visual proof because
the clean profile correctly treated the development worktree as untrusted.
Workspace trust was not disabled or silently granted.

Live microphone transcription was not started unattended. The installed
control is visible and the managed voice transport has automated coverage, but
spoken-input proof remains a human-interaction gate.

Local installed evidence:

- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-navigation-qa-20260729-final\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-settings-qa-20260729-final2\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-projects-chats-qa-20260729-final\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-attachment-qa-20260729-final\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-chat-qa-20260729-fast\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-chat-qa-20260729-final4\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-terminal-qa-20260729-final\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-modes-qa-20260729\plan\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-visual-qa-20260729-packaged-052\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-navigation-qa-20260729-packaged-052\`

## Proven source capabilities

The current source and contract suites prove the presence of these V1 building
blocks:

- one-app embedded Founder Node runtime ownership
- authenticated and replay-protected IDE IPC
- multi-workspace endpoint discovery and routing
- Founder-managed DeepSeek and personal AI profile contracts
- managed and personal visual input contracts
- managed voice transport contracts
- local Ollama compatibility
- bounded workspace context graph with symbols, imports, reverse dependencies,
  active-file PageRank seeding, and a sub-4K-token 10,000-file repository map
- prompt prefix stability, bounded tool output, safe result caching, and
  verified-solution memory
- AgentBus V2 dependencies, scope claims, fencing tokens, ledger replay, stall
  thresholds, and completion receipts
- evidence-gated workspace housekeeping with exact approved paths and restore
  receipts
- Founder-first navigation, project history, goal state, Skills, and Second
  Brain contracts
- a transparent, draggable, always-on-top Founder Dragon companion with
  resting, listening, planning, working, coordinating, verifying, delivered,
  attention, error, offline, and update states

## Gates still open

Automated source evidence is not sufficient to claim V1 complete. These gates
remain required:

1. Finish native interaction QA for live microphone transcription, Ollama,
   Skills, terminal fallback, and all five Founder modes. Packaging, installed
   navigation, settings, attachment, managed Fast chat, and visible Second
   Brain controls are proven above.
2. Prove the installed Founder Node remote pairing and authenticated action loop
   end to end without exposing a reusable pairing secret.
3. Deploy the source Auto-to-Flash policy through the normal guarded production
   process, then repeat the fresh installed `founder-os-auto` proof.
4. Audit the deployed website and admin controls against the shipped IDE
   capacity and correct stale or misleading product claims.
5. Produce a signed clean-machine installer receipt, or record signing as an
   explicit external blocker for a clearly labelled beta channel.
6. Complete clean Windows VM install/uninstall/update/rollback evidence.
7. Complete the required stability/soak period.

Until those gates have evidence, Founder V1 remains an active release goal.
