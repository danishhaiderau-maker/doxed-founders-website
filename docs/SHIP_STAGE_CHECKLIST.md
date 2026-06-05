# Ship stage checklist (Founder OS)

Use after Mission Control Ask/Resume/Build routing is validated.

## A — Ship (code + production) ✓

- [x] **Close or replace PR #2** — closed; hardening on `master`.
- [x] **Production deploy** — `npm run sync:all` (Neon, Railway API, Vercel).
- [x] **API header** — Railway `/api/health` has no `X-Powered-By: Express`.
- [x] **ZAP** — `Security ZAP Baseline` workflow_dispatch green (run 26975147426).

## B — Publish (product) ✓

- [x] **Founder queue → Publish** — `npm run stage-b:publish` (2 updates → feed, X, community).
- [ ] Confirm posts visible on **Feed** / **Social Hub** / X in browser (hard refresh).
- [ ] Optional: merge any *new* PRs from Founder queue.

## C — Android vault (validation) ← next after B

- [ ] Install APK v0.3.0 on a physical device.
- [ ] Pair Founder Node / vault; trigger pull + merge from production API.
- [ ] Confirm conflict behavior and that private notes stay off Neon.

## D — Daily use

- **Resume** — GitHub + vault briefing (no auto-build).
- **Ask [Ollama / DeepSeek / …]** — status and planning.
- **Build with Cursor** — repo edits from chat.
