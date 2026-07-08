# Demo cassettes

The end-to-end demo harness (`scripts/demo-harness.mjs`) never calls real
paid APIs by default. Instead it replays **cassettes** — JSON fixtures
recorded once and re-read on every run — so the demo is fast, free, and
deterministic. The Python bot and the NestJS API share the same cassette
directory and file layout so AI verdicts stay identical across both
runtimes.

## Layout

```
cassettes/
  deepseek/      DeepSeek chat completion responses (3 verdicts)
  bitfinex/      Bitfinex public market data snapshots
  relay/         Relay webhook payloads (approve -> place -> update -> close)
```

Each cassette is a JSON object shaped:

```json
{
  "key": "<stable hash or slug>",
  "capturedAt": "<iso timestamp>",
  "mode": "replay" | "capture",
  "request": { "...": "what was asked" },
  "response": { "...": "what was returned" }
}
```

The orchestrator reads `cassette.response` as the payload it forwards.

## Replay vs capture

- **replay** (default): read-only. A miss returns `null` / a sensible
  fallback. No external calls. This is what `npm run demo:full` uses.
- **capture**: refresh the cassettes from the real APIs once, then commit
  them. Enable with `DEMO_CAPTURE=1` or `--capture`:

  ```powershell
  $env:DEMO_CAPTURE = "1"
  npm run demo:capture
  ```

  Capture writes new entries under `cassettes/<bucket>/<key>.json`. Only
  run capture from a trusted environment with the real API keys set.

## Stable AI keys

The DeepSeek cassette key is
`sha1(model + "|" + temperature + "|" + promptPrefix[:256])[:24]`. This
hash is implemented identically in `apps/api/src/demo/cassette-store.ts`
(`deepseekKey`) and `services/btc-conservative-agent/demo_mode.py`
(`deepseek_key`). The default cassettes use the slug
`approve_long_default` / `approve_short_default` / `reject_default` so
they are easy to read; the runtime hashes the live prompt and falls back
to these named defaults when no hash match exists.

## Editing a cassette

Cassettes are committed JSON — edit them directly when a verdict shape
changes. The scorecard (`readiness-scorecard.service.ts`) does not depend
on the exact numbers inside; it only checks that the relay cycle
completes and that AI verdicts carry a non-`AI_ERROR` decision.
