# Portal resume and analyzer mirror diagnosis

Read-only verification on 5 September 2026. No trading controls clicked.

- Authenticated Fly `/api/status`: df45887e1526, paper-only, execution allowed/unpaused, Bitfinex disabled, relay disarmed.
- `/ready`: scheduled cycle completed_ts=1788568976.2121038, last_poll_reason=READY, entry eligible; zero pending orders and zero open positions in this sample. This is one completed-cycle receipt, not a sustained two-cycle soak.
- Read the user's authenticated Edge Agent Hub tab: Fly and signed feed online; Analyzer mirror feed stale, about 62.5h old; Pause trading button visible.
- `/api/analyzer-mirror/status`: bundle available/complete, uploaded September 2 at 20:13:30 AEST; source_data_revision 9b588c0b5f79; analyzer code revision 10db40b8e65393043f33db98e6c1d6e3ad7ddc27; embedded analyzer_generated_at August 29 at 04:58:26 AEST. A complete old bundle is not current data.
- Live sync PID16576 verified; heartbeat September 5 00:43:55Z, chunk_complete, file1954/34433. Mirror retained9b588c0, observed Flydf45887, parityMISMATCH. No terminal ACK claimed.

## Cause and next action

The paper pause and research publication are separate controls. User resume cleared ADMIN_MANUAL; it does not upload analyzer results. Raw Fly-to-laptop download must finish and promote, then the analyzer must build a current atomic generation and upload its verified report bundle to Fly. Preserve the running transfer; do not start a duplicate or erase source evidence.

Source trace: agent-admin-showcase-control.tsx reads getBotHealth.analyzerMirror; trading-agents.service.ts fetches Fly's mirror receipt; public-bot-health-probe.ts uses a 24-hour upload-age threshold. Follow-up QA: classify source-generation mismatch separately from upload age, so reuploading an old report cannot imply current strategy evidence.

Second user screenshot names founder-next's codex-windows-sandbox-setup.exe and reports a missing module. It is a separate local helper load error; the screenshot does not identify the missing dependency or prove a Fly failure. No local runtime repair attempted in this diagnosis.
