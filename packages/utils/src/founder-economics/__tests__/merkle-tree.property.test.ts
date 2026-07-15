/**
 * Property tests for the Merkle tree (Phase 8 — Founder Economics).
 *
 * Run with `npm test --workspace=@dcf/utils`.
 *
 * Coverage:
 *   1. Leaf hash byte-matches `keccak256(abi.encode(address, amount))` —
 *      the exact leaf the on-chain EpochDistributor.claim computes.
 *   2. For arbitrary trees (1..200 leaves) every leaf's proof verifies
 *      against the root using the same Keccak-256 hashPair OZ uses.
 *   3. Round-trip: buildMerkleTree → findProof → verifyMerkleProof.
 *   4. Edge cases: single leaf, duplicate leaves, empty tree, 18-decimal
 *      amounts that would overflow JS number (20M × 10^18).
 *   5. Determinism: same leaves in different orders produce the same root
 *      (the dedup is case-insensitive on address + amount).
 *   6. Negative path: a tampered amount fails verification.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { AbiCoder, keccak256, getBytes } from 'ethers';
import {
  buildMerkleTree,
  encodeLeaf,
  findProof,
  hashLeaf,
  hashPair,
  verifyMerkleProof,
  type MerkleLeaf,
} from '../merkle-tree';

const abiCoder = new AbiCoder();

function randomAddress(seed: number): string {
  let hex = '0x';
  for (let i = 0; i < 40; i++) {
    hex += ((seed * 31 + i * 7) % 16).toString(16);
  }
  return hex;
}

function randomLeaves(count: number, seed = 0xC0FFEE): MerkleLeaf[] {
  const out: MerkleLeaf[] = [];
  for (let i = 0; i < count; i++) {
    out.push({
      walletAddress: randomAddress(seed + i),
      amount: BigInt(((seed * (i + 1)) % 1000) + 1) * 1_000_000n,
    });
  }
  return out;
}

describe('hashLeaf — byte-identical to keccak256(abi.encode(account, amount))', () => {
  it('matches for a small whole-token amount', () => {
    const leaf: MerkleLeaf = {
      walletAddress: '0x' + '1'.repeat(40),
      amount: 1_000n,
    };
    const offChain = '0x' + Buffer.from(hashLeaf(leaf)).toString('hex');
    const expected = keccak256(
      abiCoder.encode(['address', 'uint256'], [leaf.walletAddress, leaf.amount]),
    );
    assert.equal(offChain, expected);
  });

  it('matches for 20M × 10^18 (overflows JS number, fits uint256)', () => {
    const leaf: MerkleLeaf = {
      walletAddress: '0x' + 'ab'.repeat(20),
      amount: 20_000_000n * 10n ** 18n,
    };
    const offChain = '0x' + Buffer.from(hashLeaf(leaf)).toString('hex');
    const expected = keccak256(
      abiCoder.encode(['address', 'uint256'], [leaf.walletAddress, leaf.amount]),
    );
    assert.equal(offChain, expected);
  });

  it('matches for the max uint256 value', () => {
    const leaf: MerkleLeaf = {
      walletAddress: '0x' + 'ff'.repeat(20),
      amount: 2n ** 256n - 1n,
    };
    const offChain = '0x' + Buffer.from(hashLeaf(leaf)).toString('hex');
    const expected = keccak256(
      abiCoder.encode(['address', 'uint256'], [leaf.walletAddress, leaf.amount]),
    );
    assert.equal(offChain, expected);
  });

  it('normalizes mixed-case addresses (lowercase) before hashing', () => {
    const lower: MerkleLeaf = {
      walletAddress: '0x' + 'a'.repeat(40),
      amount: 5n,
    };
    const mixed: MerkleLeaf = {
      walletAddress: '0x' + 'A'.repeat(40),
      amount: 5n,
    };
    // The dedup + leaf encode lowercases; abi.encode of a checksummed vs
    // lowercase address produces the same bytes (address is case-insensitive).
    assert.deepEqual(
      Buffer.from(hashLeaf(lower)),
      Buffer.from(hashLeaf(mixed)),
    );
  });

  it('encodeLeaf produces 64 bytes (32-byte address + 32-byte uint256)', () => {
    const leaf: MerkleLeaf = {
      walletAddress: '0x' + '12'.repeat(20),
      amount: 42n,
    };
    const encoded = encodeLeaf(leaf);
    assert.equal(encoded.length, 64);
    // abi.encode places address right-aligned in word 0, amount in word 1.
    // Address 0x1212...12 → bytes 12..31 of word 0.
    assert.equal(encoded[12], 0x12);
    assert.equal(encoded[31], 0x12);
    // amount=42 → last byte of word 1.
    assert.equal(encoded[63], 42);
  });
});

describe('hashPair — Keccak-256 of sorted concatenation', () => {
  it('produces 32-byte Keccak-256 digests', () => {
    const a = getBytes(keccak256('0x01'));
    const b = getBytes(keccak256('0x02'));
    const parent = hashPair(a, b);
    assert.equal(parent.length, 32);
  });

  it('is symmetric — hashPair(a,b) === hashPair(b,a)', () => {
    const a = getBytes(keccak256('0x01'));
    const b = getBytes(keccak256('0x02'));
    const ab = '0x' + Buffer.from(hashPair(a, b)).toString('hex');
    const ba = '0x' + Buffer.from(hashPair(b, a)).toString('hex');
    assert.equal(ab, ba);
  });
});

describe('Round-trip: buildMerkleTree → findProof → verifyMerkleProof', () => {
  for (const n of [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 32, 33, 100, 200]) {
    it(`every leaf verifies in a ${n}-leaf tree`, () => {
      const leaves = randomLeaves(n, 1000 + n);
      const tree = buildMerkleTree(leaves);
      assert.equal(tree.hashFn, 'keccak256');
      for (const leaf of tree.leaves) {
        const entry = findProof(tree, leaf.walletAddress, leaf.amount);
        assert.ok(entry, `missing proof for ${leaf.walletAddress}`);
        assert.ok(
          verifyMerkleProof(tree.root, leaf, entry.proof),
          `proof failed for ${leaf.walletAddress} (len=${entry.proof.length})`,
        );
      }
    });
  }

  it('root is unchanged when input order is shuffled (dedup + sort by hash)', () => {
    const leaves = randomLeaves(8, 4242);
    const root1 = buildMerkleTree(leaves).root;
    const root2 = buildMerkleTree([...leaves].reverse()).root;
    assert.equal(root1, root2);
  });

  it('dedups identical (wallet, amount) leaves', () => {
    const leaf: MerkleLeaf = {
      walletAddress: '0x' + '7'.repeat(40),
      amount: 99n,
    };
    const tree = buildMerkleTree([leaf, leaf, leaf]);
    assert.equal(tree.leaves.length, 1);
  });

  it('a tampered amount fails verification', () => {
    const leaves = randomLeaves(5, 555);
    const tree = buildMerkleTree(leaves);
    const real = tree.leaves[0]!;
    const tampered: MerkleLeaf = {
      walletAddress: real.walletAddress,
      amount: real.amount + 1n,
    };
    const realProof = findProof(tree, real.walletAddress, real.amount)!.proof;
    assert.equal(
      verifyMerkleProof(tree.root, tampered, realProof),
      false,
      'tampered amount must NOT verify against the real proof',
    );
  });
});

describe('Edge cases', () => {
  it('empty tree → zero root, no leaves, no proofs', () => {
    const tree = buildMerkleTree([]);
    assert.equal(tree.root, '0x' + '0'.repeat(64));
    assert.equal(tree.leaves.length, 0);
    assert.deepEqual(tree.proofs, {});
    assert.equal(tree.hashFn, 'keccak256');
  });

  it('single-leaf tree → root equals the leaf hash, empty proof', () => {
    const leaf: MerkleLeaf = {
      walletAddress: '0x' + '3'.repeat(40),
      amount: 1n,
    };
    const tree = buildMerkleTree([leaf]);
    const leafHash = '0x' + Buffer.from(hashLeaf(leaf)).toString('hex');
    assert.equal(tree.root, leafHash);
    const entry = findProof(tree, leaf.walletAddress, leaf.amount)!;
    assert.deepEqual(entry.proof, []);
    assert.ok(verifyMerkleProof(tree.root, leaf, entry.proof));
  });

  it('duplicate-but-distinct leaves (same wallet, different amounts) all verify', () => {
    const wallet = '0x' + 'f'.repeat(40);
    const leaves: MerkleLeaf[] = [
      { walletAddress: wallet, amount: 10n },
      { walletAddress: wallet, amount: 20n },
      { walletAddress: wallet, amount: 30n },
    ];
    const tree = buildMerkleTree(leaves);
    // dedup key is wallet+amount, so 3 distinct leaves survive.
    assert.equal(tree.leaves.length, 3);
    for (const leaf of tree.leaves) {
      const entry = findProof(tree, leaf.walletAddress, leaf.amount);
      assert.ok(entry);
      assert.ok(
        verifyMerkleProof(tree.root, leaf, entry!.proof),
        `duplicate-wallet leaf ${leaf.amount.toString()} must verify`,
      );
    }
  });

  it('18-decimal amounts (20M × 10^18) verify end-to-end', () => {
    const leaves: MerkleLeaf[] = [
      { walletAddress: '0x' + '11'.repeat(20), amount: 5_000_000n * 10n ** 18n },
      { walletAddress: '0x' + '22'.repeat(20), amount: 7_500_000n * 10n ** 18n },
      { walletAddress: '0x' + '33'.repeat(20), amount: 7_500_000n * 10n ** 18n },
    ];
    const tree = buildMerkleTree(leaves);
    for (const leaf of tree.leaves) {
      const entry = findProof(tree, leaf.walletAddress, leaf.amount)!;
      assert.ok(
        verifyMerkleProof(tree.root, leaf, entry.proof),
        'large 18-decimal amount must verify',
      );
    }
  });
});
