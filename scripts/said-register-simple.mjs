#!/usr/bin/env node
/**
 * SAID registration WITHOUT Phantom browser popups.
 * Run on YOUR PC only — never share the generated wallet file.
 *
 * Usage:
 *   node scripts/said-register-simple.mjs
 *   node scripts/said-register-simple.mjs --verify
 *
 * Steps:
 *   1. Script creates agent-wallet.json (or uses existing)
 *   2. You send ~0.02 SOL to the printed address (any exchange → Solana)
 *   3. Run again — registers + optional verify on SAID
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Keypair, Connection, LAMPORTS_PER_SOL } from '@solana/web3.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const walletPath = join(root, 'agent-wallet.json');
const METADATA_URI = 'https://doxxedcrypto.digital/.well-known/agent-card.json';
const AGENT_NAME = 'Conservative BTC Agent';
const RPC = process.env.SOLANA_RPC_URL || 'https://api.mainnet-beta.solana.com';
const verify = process.argv.includes('--verify');

function loadOrCreateWallet() {
  if (existsSync(walletPath)) {
    const raw = JSON.parse(readFileSync(walletPath, 'utf8'));
    const secret = Uint8Array.from(raw);
    return Keypair.fromSecretKey(secret);
  }
  const kp = Keypair.generate();
  writeFileSync(walletPath, JSON.stringify(Array.from(kp.secretKey)));
  console.log('\n=== NEW WALLET CREATED ===');
  console.log('Saved to:', walletPath);
  console.log('KEEP THIS FILE PRIVATE. Do not commit or send to anyone.\n');
  return kp;
}

async function main() {
  const kp = loadOrCreateWallet();
  const address = kp.publicKey.toBase58();
  console.log('Agent wallet (fund this with SOL on Solana mainnet):');
  console.log(' ', address);
  console.log('Metadata URI:', METADATA_URI);

  const conn = new Connection(RPC, 'confirmed');
  const bal = await conn.getBalance(kp.publicKey);
  console.log('\nBalance:', (bal / LAMPORTS_PER_SOL).toFixed(4), 'SOL');

  const minSol = verify ? 0.025 : 0.005;
  if (bal < minSol * LAMPORTS_PER_SOL) {
    console.log(`\n>>> Send at least ${minSol} SOL to the address above, then run this script again.`);
    console.log('    (Use Coinbase, Binance, or Phantom — withdraw to Solana mainnet)\n');
    process.exit(0);
  }

  let SAID;
  try {
    SAID = (await import('said-sdk')).SAID;
  } catch {
    console.error('\nInstall said-sdk first: npm install said-sdk @solana/web3.js');
    process.exit(1);
  }

  const said = new SAID({ rpcUrl: RPC });
  const registered = await said.isRegistered(kp.publicKey);

  if (!registered) {
    console.log('\nRegistering on SAID (free, ~0.001 SOL gas)...');
    const result = await said.registerAgent(kp, METADATA_URI, kp);
    console.log('Registered! Tx:', result.txSignature);
    console.log('Agent PDA:', result.agentPDA);
  } else {
    console.log('\nAlready registered on SAID.');
  }

  if (verify) {
    const verified = await said.isVerified(kp.publicKey);
    if (!verified) {
      console.log('\nVerifying badge (~0.01 SOL)...');
      const v = await said.verifyAgent(kp);
      console.log('Verified! Tx:', v.txSignature);
    } else {
      console.log('\nAlready verified on SAID.');
    }
  } else {
    console.log('\nOptional verified badge: node scripts/said-register-simple.mjs --verify');
  }

  console.log('\n=== DONE ===');
  console.log('Add this address as admin Solana treasury in Admin → Agent registrations');
  console.log('Check: https://api.saidprotocol.com/api/verify/' + address);
  console.log('\nAdd agent-wallet.json to .gitignore (already should be). Never share it.\n');
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
