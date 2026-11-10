# Local read-only tiles viewer (DRAFT; merge/start gates still apply)

## Scope and safety

`dashboard_viewer.py` serves the existing main dashboard's literal `HTML` and
`DASHBOARD_JS`, with the same `renderPathwayLab` tile handler. It reads those
constants through Python AST literal extraction: **bot.py is never imported or
executed**. There is no second implementation of the tiles or policy renderer.
The existing data-only registry and card-text helpers supply the current roster.

Only `/`, `/static/dashboard.js`, `/static/dashboard-viewer.js`, `/api/state`
and `/health` are registered. No control, login, export, download, live-arm or
trade routes exist. Non-GET/HEAD requests return 405, including unknown routes.
Other GETs return 404. Host headers are restricted to the configured loopback
port (DNS-rebinding defense). Flask static-directory serving is disabled.

The server **always binds 127.0.0.1**, default port 7002, with no host override,
debugger, reloader or request threads. It has no engine, exchange client,
collector, writer, background worker, DB connection, network data fetch or
subprocess. The HTTP serving loop is the only server loop. Logs go to stderr;
`-B` prevents bytecode writes. Nothing creates a data directory or changes an
existing file, scheduler entry, firewall, power policy or live/arm state.

Any live/arm environment name, any `BITFINEX_*`/`BFX_*` name, and known remote
credential/control names (including `GH_TOKEN`, `DATABASE_URL`, `BOT_ADMIN_TOKEN`)
refuse startup. Presence is sufficient: **even `BITFINEX_LIVE_ENABLED=0` or an
empty key refuses**. Do not import a vault/cloud environment or use the old
paper-bot launcher. Diagnostics never print environment values. The check also
runs before requests. Every recognized saved arm field must be the literal JSON boolean `false`,
not null, zero, text, an object or a list. The canonical
`live_copy_output_state.json` is REQUIRED with schema `fly_live_copy_output_v1`
and `state.enabled` explicitly `false`; absent file/schema/state/enabled refuses.
A present lifecycle additionally requires `paper_only: true` and `live_armed: false`.
Enabled/uncertain saved arm state, non-paper lifecycle, invalid/oversize JSON and
paths escaping the data root fail closed. CLI refusal
exits 78; request-time refusal returns 503. No automatic restart is added.

The browser adapter selects only the existing tile view, labels it saved/read-only,
hides controls, bypasses browser setting reconciliation, and blocks fetches
except same-origin GET `/api/state` and `/health`. CSP blocks external connects,
forms and frames. The server remains read-only even with JavaScript disabled or
by direct HTTP. Saved lane output uses an explicit, recursively typed allowlist of display fields
and statistics; unknown fields at every nesting depth are dropped, not
secret-name-blacklisted. Evidence strings are escaped before the legacy
innerHTML renderer. Counts must be nonnegative integers and numeric statistics
finite; incomplete valid statistics remain unavailable.

## Proposed laptop command — NOT EXECUTED

Only after HM/RK merge and exact-revision/start approval, from a clean process
environment without the forbidden live/arm/credential names:

```powershell
Set-Location 'C:\DoxxedCrypto\v2c\services\btc-conservative-agent'
& 'C:\Users\danis\AppData\Local\Programs\Python\Python311\python.exe' -B .\dashboard_viewer.py --data-dir 'C:\DoxxedCrypto\v2c\services\btc-conservative-agent\local-fresh-data' --port 7002
```

Open `http://127.0.0.1:7002/`. Use `--port` to select another available port;
network/LAN binding is not supported. The interpreter path was observed by the
laptop finder, not certified by this PR: executable hash and installed Flask
are UNVERIFIED. Runtime dependency: Flask >=3.0 (Werkzeug is its dependency).
The viewer does not require the full trading/analyzer requirements.

**Windows/Python 3.11 NOT TESTED.** Linux Python 3.11 tests do not certify Windows
startup, dependencies, filesystem/link behavior, browser behavior or port ownership.
Nothing in this PR restarts the exit-75 bot or starts anything on the laptop.

## Precisely what is read

The root must be approved and protected from untrusted concurrent writers.
Resolve/containment and open are separate operations: this is NOT a defense
against a malicious concurrent symlink/junction swap (HM N1; Windows untested).

Under the explicit existing `--data-dir` (the proposed local-fresh-data above),
read-only binary opens, maximum **8 MiB per JSON file per request**:

- `debug_state_snapshot.json`: optional saved main-dashboard state (for example,
  the JSON member from a previously approved debug export); only its saved tile
  specs/statistics and revision are projected, not arbitrary engine/control data.
- `pathway_lane_specs.json`: existing static/saved tile specs fallback.
- `paper_lifecycle_v1.json`: optional safety validation (`paper_only` must be true
  and `live_armed` explicitly false) and saved-at timestamp only; no recovery/replay
  or trading occurs.
- `live_copy_output_state.json`: REQUIRED safety-only record, schema
  `fly_live_copy_output_v1` with current `state.enabled` explicitly boolean false.
  Historical `state.history[*].enabled` records are not the current switch and are
  not output. No sidecar metadata/credentials are projected. Missing or malformed
  records refuse; the viewer never creates, resets or edits an arm record. Do not
  manufacture a false record just to launch the viewer; an authoritative existing
  disarmed record or separately reviewed evidence/start policy is necessary.

The code also reads `bot.py` literals and `dashboard_viewer.js` in its own service
folder. It opens **no SQLite, Neon, other DB or raw collector evidence** and never
calls a live bot to obtain state. No new producer/snapshot writer is introduced.
File availability and age are displayed explicitly; saved files are NOT a current
heartbeat. No statistics snapshot is required to view the registry/policy cards after the
mandatory disarmed-sidecar check, but absent
or incomplete saved statistics show UNAVAILABLE, not fabricated zero PnL. Pending
and open-position counts are not inferred. This UI grants no PASS, promotion,
forward-paper evidence or arm permission. A separately approved producer/adapter
is still needed for new research/forward results to appear here.

## Laptop revision divergence

Baseline master: `f0590ac2dbdbb85b9ad7bc9b2b35bd5fcdaadf95`.
Observed detached laptop v2c: `4978afd087c198e35efadaa9ec4b59b765fd0310`.
Master is 41 commits ahead of that revision. Before this PR, `bot.py`,
`showcase_ui.py` and `btc_conservative_agent.py` have no content difference across
those revisions. The data-only `combo_pathway_config.py` **does differ**: master
retires B1/B2 and keeps CTRL + GS-01, unlike the older four-tile registry. This
viewer filters stored specs against the current registry, so stale B1/B2 files
do not re-add retired tiles. Do not copy only the viewer script into the old
checkout: the reviewed exact source revision must include the matching bot JS
hooks, adapter and registry. No laptop checkout/update is performed here.

## Reproduce tests on the box

In the service directory, isolated Python 3.11 with Flask, pytest and Playwright:

```sh
python -B -m playwright install chromium
python -B -m pytest -q -p no:cacheprovider test_dashboard_viewer.py
```

The browser test opens only a short-lived loopback server with a temporary
fixture, verifies actual cards/PnL rendering with the original tile handler,
checks there are no active tile buttons or external/mutating requests, and
closes the server. Import/create-app tests also assert no local runtime modules
or threads are loaded, and HTTP tests prove file bytes/mtimes remain unchanged.

### Initial-head results (eb85cc3a; 10 Nov 2026, AEDT; box only)

- Linux CPython 3.11.18, Flask 3.1.2, pytest 9.2.0, Playwright 1.65.0:
  **129 passed**, including 100 viewer tests and 29 existing presentation tests.
  Chromium rendered both current tiles and fixture $12.50 PnL without page errors.
- Existing full-service environment, Linux CPython 3.13.5:
  **52 passed** across 11 dashboard/showcase regression suites (overlaps above).
- `node --check dashboard_viewer.js` and `git diff --check`: passed.
- Existing embedded-JS invalid-escape warnings remain; no Windows result claimed.

### B1/B2 revised-head results (10 Nov 2026, AEDT; box only)

- Linux Python 3.11: **251 viewer tests passed**; combined viewer/presentation
  command from the PR body plus CI inventory: **288 passed**, 53 existing JS escape warnings.
- Linux Python 3.13 legacy dashboard/showcase regressions: **52 passed**,
  6 existing escape warnings (overlapping tests).
- B1 covers null, object, list, strings, numeric false-lookalikes and missing
  switch/file/schema/state; only boolean false passes. No live-control module
  is imported and no arm record is created/reset/changed.
- B2 tests apiKey, privateKey, credentials.key, unknown fields at supported
  nested object/list levels, and wrong-shaped known metadata fields. All are
  absent from /api/state while known fixture display/statistics still render.
- `node --check dashboard_viewer.js` and `git diff --check`: passed.
- Windows/Python 3.11 NOT TESTED; exact-head CI and a new HM/RK review remain gates.

CI registration: `ci/unit_test_allowlist.txt` now includes `test_dashboard_viewer.py`.
The inventory guard initially caught that omission; all eight inventory tests now
pass. All 250 non-browser viewer regressions, including every B1/B2 case, are
required in unit-tests. The single Chromium test uses `pytest.importorskip` only
when its optional Playwright package is absent; the package is not added to
trading/runtime dependencies. Locally Playwright WAS installed and that browser
test ran and passed (288 passed, zero skips). No workflow or known-red list changes.
