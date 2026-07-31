#!/usr/bin/env node
/**
 * Retired compatibility command. Never restart the old Railway bot service:
 * doing so can revive an image that predates the Fly single-owner guard.
 */
const action = process.argv[2] ?? 'status';
console.log(
  JSON.stringify(
    {
      ok: action === 'status',
      retired: true,
      requestedAction: action,
      railwayBotControl: 'disabled',
      canonicalRuntime: 'https://doxed-btc-bot.fly.dev',
    },
    null,
    2,
  ),
);
if (action !== 'status') process.exitCode = 78;
