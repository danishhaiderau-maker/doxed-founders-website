-- B4 (#852, 24 Oct): real-Postgres race harness for the HM1 account live-mutex + LiveEntryFence.
-- Run ONLY on a throwaway Neon branch (never the main/production branch). It applies the additive
-- 20261024040000_live_entry_fence migration, then mirrors, statement for statement, what the relay does:
--   claim  = takeLiveMutexLock (pg_advisory_xact_lock(hashtext('live-mutex:'||user))) -> countLiveMutexOthers
--            (live participants + unexpired LiveEntryFence rows) -> insert the claim only when the count is 0
--   fence  = acquireLiveEntryFence: the same advisory lock -> insert the LiveEntryFence row
-- pg_sleep(0.3) between the read and the write widens the race window. Each call runs in its own
-- connection/transaction (READ COMMITTED, as Prisma interactive transactions do). use_lock=false is the control.
CREATE TABLE "LiveEntryFence" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "reason" TEXT NOT NULL,
  "acquiredAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "LiveEntryFence_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "LiveEntryFence_userId_expiresAt_idx" ON "LiveEntryFence"("userId", "expiresAt");
CREATE SCHEMA b4race;
CREATE TABLE b4race.participant (id text PRIMARY KEY, "userId" text NOT NULL, status text NOT NULL, venue text,
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE b4race.log (n serial PRIMARY KEY, kind text, caller text, admitted boolean, others int,
  t_start timestamptz, t_locked timestamptz, t_end timestamptz, backend_pid int);
CREATE FUNCTION b4race.claim(u text, pid text, use_lock boolean) RETURNS text LANGUAGE plpgsql AS $$
DECLARE t0 timestamptz := clock_timestamp(); tl timestamptz; n int; f int;
BEGIN
  IF use_lock THEN PERFORM pg_advisory_xact_lock(hashtext('live-mutex:' || u)); END IF;
  tl := clock_timestamp();
  SELECT count(*) INTO n FROM b4race.participant
   WHERE "userId" = u AND status IN ('PENDING_ENTRY', 'OPEN') AND (venue = 'bitfinex' OR venue IS NULL);
  SELECT count(*) INTO f FROM "LiveEntryFence" WHERE "userId" = u AND "expiresAt" > clock_timestamp();
  PERFORM pg_sleep(0.3);
  IF n + f = 0 THEN
    INSERT INTO b4race.participant (id, "userId", status, venue) VALUES (pid, u, 'PENDING_ENTRY', 'bitfinex');
  END IF;
  INSERT INTO b4race.log (kind, caller, admitted, others, t_start, t_locked, t_end, backend_pid)
  VALUES ('claim', pid, n + f = 0, n + f, t0, tl, clock_timestamp(), pg_backend_pid());
  RETURN CASE WHEN n + f = 0 THEN 'ADMITTED' ELSE 'REFUSED:' || (n + f) END;
END $$;
CREATE FUNCTION b4race.fence(u text, fid text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE t0 timestamptz := clock_timestamp(); tl timestamptz;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('live-mutex:' || u));
  tl := clock_timestamp();
  PERFORM pg_sleep(0.3);
  INSERT INTO "LiveEntryFence" (id, "userId", reason, "expiresAt")
  VALUES (fid, u, 'B4_RACE', (clock_timestamp() + interval '120 seconds')::timestamp(3));
  INSERT INTO b4race.log (kind, caller, admitted, others, t_start, t_locked, t_end, backend_pid)
  VALUES ('fence', fid, true, null, t0, tl, clock_timestamp(), pg_backend_pid());
  RETURN 'FENCED';
END $$;
-- Race: 20 concurrent sessions for one account (1 fence + 19 claims):  SELECT b4race.fence('b4-u1','f1'); SELECT b4race.claim('b4-u1','c<i>',true);
-- Control: 10 concurrent claims without the lock:                        SELECT b4race.claim('b4-u3','x<i>',false);
-- Invariants (lock): <= 1 admitted claim per account; no claim admitted after the fence committed; locked
-- sections never overlap. Control must show > 1 admitted (proves the sessions really overlapped).
--
-- RESULTS 2026-10-24 ~04:20 AEDT, Neon throwaway branch "pr852-b4-race-test-throwaway" (br-quiet-math-ap2rieok,
-- created from production, deleted right after; production verified read-only to have no LiveEntryFence/b4race):
--   Locked race (b4-u1, 20 sessions, 4 concurrent backends, 13 overlapping session pairs, max lock wait 0.633 s):
--     1 ADMITTED (c01), 3 REFUSED:1 (c02-c04, before fence), f1 FENCED, 15 REFUSED:2 (c05-c19, claim + fence)
--     overlapping locked sections = 0; claims admitted after the fence = 0; fence rows = 1.  PASS
--   Control without lock (b4-u3, 6 concurrent sessions, 6 backends): 6/6 ADMITTED -> the race is real and the
--     advisory lock is what prevents double admission.
-- Limitation: Prisma TS code not executed (connection-string retrieval blocked by Auto-review); this reproduces the
-- relay's SQL sequence (lock -> count participants + unexpired fences -> insert) via the Neon SQL API.
