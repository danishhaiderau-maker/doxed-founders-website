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

## C — Android vault (validation) ← **current stage**

See **[STAGE_C_ANDROID_VAULT.md](./STAGE_C_ANDROID_VAULT.md)**.

- [ ] Run `npm run stage-c:probe` (server privacy check).
- [ ] Install APK **v0.4.0** from https://doxxedcrypto.digital/mobile on a physical device.
- [ ] Pair with **Code for Android** (Settings → Founder Node).
- [ ] Pull + merge vault on device; confirm `lastSyncAt` updates.
- [ ] Confirm private note bodies stay off Neon (probe + Resume copy).

## D — Daily use

- **Resume** — GitHub + vault briefing (no auto-build).
- **Ask [Ollama / DeepSeek / …]** — status and planning.
- **Build with Cursor** — repo edits from chat.
