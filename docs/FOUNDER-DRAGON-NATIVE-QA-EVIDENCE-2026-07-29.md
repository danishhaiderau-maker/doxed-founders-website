# Founder Dragon Native QA Evidence

Date: 2026-07-29

Scope: Stage 8 desktop companion only. This run did not start the production
relay, alter trading state, advance `master`, or deploy a service.

## Source and checks

- Product source: `ea45964d`
- DPI/edge-aware native drag verifier: `e049dc34`
- Founder Node TypeScript no-output validation: passed
- Founder Node suite: 62/62 passed
- Fresh isolated Founder Node TypeScript output: passed
- Console errors during native state sweep: 0
- Page errors during native state sweep: 0

The normal `apps/founder-node/dist` directory remains owned by the interactive
Windows account and rejected writes from the sandbox account. It was not
deleted or force-unlocked. The QA run compiled into a fresh artifacts
directory and used hardlinks to the already-installed Electron runtime so it
did not duplicate its roughly 325 MB payload.

## Real desktop evidence

The installed companion was enumerated as one `Founder Dragon` window owned by
the embedded Founder Node. A full virtual-desktop capture proves that the
transparent sleeping Dragon remains visible over ChatGPT while Founder IDE is
open on the other display:

`C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-desktop-live-20260729.png`

The native QA runtime used the production companion module, preload, HTML, and
four production state assets. Its evidence is:

`C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\evidence.json`

Measured results:

- transparent body: `rgba(0, 0, 0, 0)`
- idle: `dragon-idle.png`, `breathe`
- working: `dragon-working.png`, `fly`
- delivered: `dragon-success-v3.png`, `deliver`
- attention: `dragon-attention.png`, `alert`
- blocked: `dragon-attention.png`, `blocked`
- native drag: `(992, 390)` to `(920, 358)`, delta `(-72, -32)`
- visible window size: 360 x 300 logical pixels

State captures:

- `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\dragon-idle.png`
- `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\dragon-working.png`
- `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\dragon-success.png`
- `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\dragon-attention.png`
- `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\dragon-error.png`

## Persistence and interaction

After closing and restarting the isolated Electron runtime, the Dragon opened
at exactly `(920, 358)`, proving the dragged location persisted:

`C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-restart-proof-20260729\evidence.json`

Native Windows hit-testing proved:

- a transparent point resolved to the underlying Founder IDE window
- a visible Dragon point resolved to a renderer child whose root was the
  Founder Dragon window

Before the isolated QA runtime was created, the foreground window was Founder
IDE while the installed Dragon remained visible above it. This proves the
always-on-top companion did not take typing focus during its resting state.

## Acceptance

Stage 8 is accepted for the internal V1 candidate:

- one installed companion identity
- recognisable consistent Dragon across states
- transparent desktop surface
- resting nest, working flight, delivered fire, attention, and blocked states
- compact task/result card
- native drag and restart persistence
- transparent-area click-through and visible-body interaction
- no console or page errors

Trusted signing, clean-machine installation, updater rollback, and soak remain
Stage 11 release gates and are not implied by this Stage 8 acceptance.

