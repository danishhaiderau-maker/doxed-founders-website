# Ship stage checklist (Founder OS)

Use after Mission Control Ask/Resume/Build routing is validated.

## A — Ship (code + production)

- [ ] **Close or replace PR #2** — branch is ~684 commits behind `master`; do not merge as-is. Hardening is on `master` (headers, batched memory sync, Cursor repo binding).
- [ ] **Production deploy** — `npm run sync:all` (Neon, Railway API, Vercel).
- [ ] **API header** — `curl -I` Railway `/api/health` should not return `X-Powered-By: Express`.
- [ ] **ZAP** — run GitHub Action `Security ZAP Baseline` (workflow_dispatch) on `doxxedcrypto.digital`.

## B — Publish (product)

- [ ] Mission Control → **Attention** → **Publish** pending founder updates (feed / X / community).
- [ ] Optional: merge any *new* PRs created after PR #2 cleanup.

## C — Android vault (validation)

- [ ] Install APK v0.3.0 on a physical device.
- [ ] Pair Founder Node / vault; trigger pull + merge from production API.
- [ ] Confirm conflict behavior and that private notes stay off Neon.

## D — Daily use

- **Ask** — status and “what am I working on?”
- **Run build** — only when Cursor should edit the repo.
- **Resume** — briefing only (no auto-Cursor).
