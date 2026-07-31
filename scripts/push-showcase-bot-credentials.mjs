#!/usr/bin/env node
/**
 * Retired compatibility command.
 *
 * It formerly decrypted production credentials and redeployed a second
 * Railway strategy process. Fly secrets are now managed directly on the
 * canonical doxed-btc-bot app and are never copied to Railway.
 */
console.log('RETIRED: Railway showcase credential push is disabled.');
console.log('Canonical runtime: https://doxed-btc-bot.fly.dev');
console.log('Use reviewed Fly.io deployment secrets for doxed-btc-bot.');
