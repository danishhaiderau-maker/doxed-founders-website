# BTC Signal Engine — Generated Compatibility Mirror

This folder is not a second bot and is not an independently editable strategy.

`engine.py` must be the normalized byte-for-byte mirror of:

```text
services/btc-conservative-agent/bot.py
```

The canonical source-to-mirror direction is always:

```text
btc-conservative-agent/bot.py
              ↓  scripts/mirror-signal-engine.mjs
btc-signal-engine/engine.py
```

It exists for CI parity validation, source fingerprints, probes, and legacy
consumers that still expect the old folder name. No production launcher,
Fly.io process, Railway service, or desktop task may run `engine.py` as a
separate strategy owner.

Never edit `engine.py` directly. Never use the deprecated research/local sync
scripts to overwrite the canonical bot. After a canonical bot change:

```bash
npm run mirror:signal-engine
npm run verify:signal-parity
```

`manifest.json` records the canonical source path and fingerprint. Its
timestamp/fingerprint changes when the safe mirror command runs.
