# Founder Product Seven-Stage Completion Plan

| Field | Value |
|---|---|
| Updated | 2026-07-22 |
| Owner | Founder product release |
| Branch | `codex/founder-ide-live-actions` |
| Rule | A stage is complete only when its user journey has runtime evidence |

## 1. Preserve and audit the work

Status: Complete.

- Reviewed the active branch, recent commits, uncommitted files, installer inputs, and built output.
- Preserved valuable AI, navigation, entitlement, companion, and coordination work.
- Kept trading bot and analyzer changes in their separate owning workstream.
- Removed a misleading QA script that could type a command into chat instead of invoking the command.

## 2. Make the AI composer usable and truthful

Status: Complete in the IDE. Production entitlement deployment remains a tracked external release item.

- Added microphone input and visible personal-AI access beside the composer.
- Added remembered BYOK providers: OpenAI, Anthropic, DeepSeek, Gemini, OpenRouter, and OpenAI-compatible endpoints.
- Kept Founder Managed as the simple default with Auto, Fast, Build, and Deep intents.
- Added plan and usage presentation for the 5,000,000-token Founder Free allowance.
- When the production entitlement endpoint is unavailable, the UI says live usage is unavailable instead of inventing a number.
- Verified a clean installed native chat response through `deepseek/deepseek-v4-pro` with a route receipt.

## 3. Replace legacy navigation and branding

Status: Complete for the current IDE shell.

- Added direct New chat, Projects, Chats, Agents, and Graph actions.
- Kept useful coding tools under a clear Build and ship section.
- Removed visible Void labels from the patched native surfaces and settings entry.
- Kept the source-control graph because it communicates branch history unusually well.

## 4. Ship the living Founder Dragon

Status: Complete and verified in the installed application.

- Runs in its own transparent, frameless, always-on-top desktop window outside the IDE.
- Remains alive when the IDE is minimized or closed because the embedded Founder Node relay is a detached background process.
- Can be dragged anywhere; its position is restored after restart and clamped to a visible display.
- Transparent areas pass clicks through to the application beneath it.
- Idle rests in a nest, working flies, success breathes fire, and attention/error states ask for a decision.
- A compact task bubble appears only when it communicates active work, delivery, or a blocker.
- Respects reduced-motion preferences and can be hidden from Founder controls.
- The retired video loops and background-heavy success image are removed from clean packages.

## 5. Coordinate agents automatically

Status: Complete for local workspace awareness.

- Added a shared work ledger containing agent identity, task, owned files, status, and heartbeat.
- Detects nearby and overlapping ownership before two agents make incompatible edits.
- Keeps unrelated work independent while surfacing close work for coordination.
- Uses bounded ownership instead of an arbitrary number of agents.
- Requires tests, evidence, and a completion gate before work is presented as finished.

## 6. Prove the install end to end

Status: Complete for local release acceptance. Production signing remains an external release gate.

- Rebuilt the one-click Founder IDE installer from the cached compiled IDE payload without recompiling the editor core.
- Cleared Electron Builder output before packaging and proved the retired Dragon asset is absent after an in-place upgrade.
- Installed into the normal Windows application location and verified the embedded Founder Node runtime.
- Archived obsolete external Founder extension copies without touching credentials or workspaces.
- Repaired and verified the desktop, Start Menu, and existing taskbar shortcuts against the installed executable.
- Passed a clean-profile navigation, branding, Personal AI, BYOK, custom-model, account, plan, and 5,000,000-token allowance sweep with no renderer errors.
- Passed a live native chat prompt through `deepseek/deepseek-v4-pro` in 3.5 seconds with the Founder route receipt visible.
- Passed installed Dragon idle, flight, fire-delivery, attention, blocked, transparency, and exact `48 x 32` pixel drag checks.
- Passed 94 extension tests and 36 embedded-node tests.
- Final installer SHA-256: `1067799EA2D51D40929E7F9F6FECC7EA730951EB79B316E7B2F26B9A17469536`.
- Authenticode is truthfully `NotSigned`; stable publishing remains blocked until a valid signing identity is provisioned.

## 7. Redesign the whole product shell

Status: In progress. The shared shell and primary account/admin information architecture are complete; signed-in deep-route redesign remains.

- Completed: replaced the website's competing menus with Build, Discover, and Trade.
- Completed: kept Search, Founder Chat, Notifications, and Account as the permanent utilities.
- Completed: merged Founder OS and Founder Den language into Build and now presents Founder IDE as the single downloadable desktop product with embedded Founder Node.
- Completed: merged Account into Profile, Security and Connections, Plan and Usage, and Inbox and History.
- Completed: merged Admin into Operations, AI and Usage, Treasury and Launch, and Trust and Safety.
- Completed: applied neutral system colors, 8px maximum shell/card radii, consistent motion, and reduced-motion handling to the shared shell and redesigned primary surfaces.
- Completed: passed a 15-screen signed-out production-build sweep across 1440x900, 1024x768, and 390x844 with no viewport overflow, console errors, page errors, or failed network responses; every Build menu remained inside its viewport.
- Unify Founder Brain credentials, routing, health, budgets, tests, and receipts without exposing provider complexity to ordinary founders.
- Remaining: apply the shared neutral visual system route by route inside Build, Discover, Trade, Connect, launch, and future DCF Swap/acquisition surfaces without changing truthful product capabilities.
- Preserve working APIs and use compatibility redirects while removing duplicate navigation, dead actions, hard-coded promo copy, raw provider errors, obsolete product names, and unnecessary panels.
- Remaining: run the same route sweep with authenticated founder and admin sessions after the redesigned deep routes are complete.

## Completion rule

Founder IDE is not considered release-ready until Stage 6 passes. The broader Founder product is not considered complete until Stage 7 passes and the production entitlement route, signing identity, and capacity test are live.
