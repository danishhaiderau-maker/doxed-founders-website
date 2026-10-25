# HANDOFF — Danish action required (paper paused ~3h+)

**One click only.** Agents cannot cancel this Actions run (GitHub returns 403).

## Do this now

1. Open: https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40169802400  
2. Click **Cancel workflow** (hung on “Complete receipt bootstrap…”).  
3. Reply here: **cancelled** (or paste the run status).

That is the whole ask. Do **not** call `/api/resume`, Soft B, Force, wipe, tip, or arm live.

## What happens next (agents)

After cancel:

1. Agent runs guaranteed paper resume (bootstrap waived) so paper collects again.  
2. Queued **#900** deploy starts (25m bootstrap timeout + auto paper resume).  
3. **#904** sticky-lease fix merges when CI is green.  
4. Agent proves paper unpaused ≥10 minutes; live stays **DISARMED**.

## Why cancel is required

Run `40169802400` is stuck on receipt bootstrap (`SOURCE_LEDGER_TRUNCATED`). While it runs, any paper resume is re-paused within seconds by the failure-path preserve step. Retrying deploy does not help until this run is cleared from the `fly-btc-production` queue.
