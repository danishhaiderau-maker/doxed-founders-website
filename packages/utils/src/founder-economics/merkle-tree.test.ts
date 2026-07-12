import assert from 'node:assert/strict';
import test from 'node:test';
import { allocateTokenUnits } from './distribution-models/base-distribution-model';
import {
  TOKEN_UNIT,
  buildMerkleTree,
  findProof,
  hashLeaf,
  verifyMerkleProof,
} from './merkle-tree';

const ALICE = '0x0000000000000000000000000000000000000001';
const BOB = '0x0000000000000000000000000000000000000002';
const CAROL = '0x0000000000000000000000000000000000000003';

test('allocates every uint256 unit deterministically, including rounding dust', () => {
  const first = allocateTokenUnits(20_000_000, [
    { walletAddress: CAROL, score: 3.125 },
    { walletAddress: ALICE, score: 1 },
    { walletAddress: BOB, score: 2 },
  ]);
  const second = allocateTokenUnits(20_000_000, [
    { walletAddress: BOB, score: 2 },
    { walletAddress: CAROL, score: 3.125 },
    { walletAddress: ALICE, score: 1 },
  ]);
  const expected = 20_000_000n * TOKEN_UNIT;

  assert.equal(first.reduce((sum, leaf) => sum + leaf.amount, 0n), expected);
  assert.deepEqual(first, second, 'input order cannot alter a settlement allocation');
});

test('uses Solidity abi.encode leaves and OpenZeppelin-compatible sorted proofs', () => {
  const tree = buildMerkleTree([
    { walletAddress: ALICE, amount: 7n * TOKEN_UNIT + 1n },
    { walletAddress: BOB, amount: 13n * TOKEN_UNIT + 2n },
    { walletAddress: CAROL, amount: 19n * TOKEN_UNIT + 3n },
  ]);
  for (const leaf of tree.leaves) {
    const proof = findProof(tree, leaf.walletAddress, leaf.amount);
    assert.ok(proof);
    assert.equal(verifyMerkleProof(tree.root, leaf, proof.proof), true);
  }
  assert.match(hashLeaf({ walletAddress: ALICE, amount: 1n }), /^0x[0-9a-f]{64}$/);
});

test('rejects duplicate claimant wallets before a root is built', () => {
  assert.throws(
    () => buildMerkleTree([
      { walletAddress: ALICE, amount: 1n },
      { walletAddress: ALICE.toUpperCase().replace('0X', '0x'), amount: 2n },
    ]),
    /Duplicate claimant wallet/,
  );
});
