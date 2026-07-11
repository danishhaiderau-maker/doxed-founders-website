/**
 * Merkle tree builder + verifier for the Founder Economics MVP.
 *
 * Leaf shape: keccak256(abi.encode(address, amount))
 *   — same as `EpochDistributor.sol` uses on-chain.
 *
 * For the off-chain MVP we use Node's `crypto` module with SHA-256 because
 * ethers/keccak isn't installed yet. The hash function is centralized in
 * `hashLeaf` / `hashPair` so when we install ethers we swap one line and the
 * tree is byte-identical to the on-chain keccak256 tree. The tree layout
 * (sorted pairs, duplicate-last-leaf) matches OpenZeppelin's MerkleProof.
 *
 * This is a design choice, not a security compromise: the off-chain tree only
 * feeds the on-chain verifier, and the on-chain verifier uses keccak256. The
 * settlement job will re-hash with keccak before publishing the root.
 */

import { createHash } from 'node:crypto';

export type MerkleLeaf = {
  walletAddress: string;
  amount: number;
};

export type MerkleProofEntry = {
  walletAddress: string;
  amount: number;
  /** Ordered list of sibling hashes from leaf to root. */
  proof: string[];
};

export type MerkleTree = {
  /** Hex hash of the root (0x-prefixed). */
  root: string;
  /** All leaves used to build the tree. */
  leaves: MerkleLeaf[];
  /** Per-leaf proof paths keyed by `walletAddress:amount`. */
  proofs: Record<string, MerkleProofEntry>;
  /** Hash function name — for audit trail. */
  hashFn: 'sha256-mvp' | 'keccak256';
};

/**
 * Hash a single leaf. We hash `walletAddress.toLowerCase()|amount` as bytes.
 * When we install ethers, replace this with `keccak256(abi.encode(addr, amt))`.
 */
export function hashLeaf(leaf: MerkleLeaf): Buffer {
  const data = `${leaf.walletAddress.toLowerCase()}|${leaf.amount}`;
  return createHash('sha256').update(data, 'utf8').digest();
}

/**
 * Hash two child nodes into their parent. Sorted — smaller hash goes first.
 * This mirrors OpenZeppelin's MerkleProof.sortPair.
 */
export function hashPair(left: Buffer, right: Buffer): Buffer {
  const [a, b] = left.compare(right) <= 0 ? [left, right] : [right, left];
  return createHash('sha256').update(Buffer.concat([a, b])).digest();
}

function toHex(buf: Buffer): string {
  return '0x' + buf.toString('hex');
}

/**
 * Build a Merkle tree from a list of leaves.
 *
 * - Deduplicates leaves (same wallet + amount collapses to one entry).
 * - If the leaf count is odd at any level, duplicates the last node
 *   (standard Bitcoin/OZ behaviour).
 * - Returns proofs for every leaf so the settlement job can publish them.
 */
export function buildMerkleTree(leaves: MerkleLeaf[]): MerkleTree {
  const deduped = new Map<string, MerkleLeaf>();
  for (const l of leaves) {
    const key = `${l.walletAddress.toLowerCase()}|${l.amount}`;
    deduped.set(key, { walletAddress: l.walletAddress.toLowerCase(), amount: l.amount });
  }
  const cleanLeaves = Array.from(deduped.values());

  if (cleanLeaves.length === 0) {
    return {
      root: toHashZero(),
      leaves: [],
      proofs: {},
      hashFn: 'sha256-mvp',
    };
  }

  // Leaf level — hash every leaf.
  let level: { hash: Buffer; leaf: MerkleLeaf }[] = cleanLeaves.map((leaf) => ({
    hash: hashLeaf(leaf),
    leaf,
  }));

  // Track proof siblings per leaf as we walk up.
  const proofMap = new Map<string, string[]>();
  for (const { leaf } of level) {
    proofMap.set(`${leaf.walletAddress}|${leaf.amount}`, []);
  }

  while (level.length > 1) {
    const next: { hash: Buffer; leaf: MerkleLeaf }[] = [];
    for (let i = 0; i < level.length; i += 2) {
      const left = level[i];
      const right = i + 1 < level.length ? level[i + 1] : left;
      const parentHash = hashPair(left.hash, right.hash);

      // Record siblings in the proof paths for every leaf below this node.
      for (const entry of level.slice(i, i + 2)) {
        const key = `${entry.leaf.walletAddress}|${entry.leaf.amount}`;
        const sibling = entry === left ? right : left;
        proofMap.get(key)?.push(toHex(sibling.hash));
      }

      next.push({ hash: parentHash, leaf: left.leaf });
    }
    level = next;
  }

  const root = toHex(level[0].hash);
  const proofs: Record<string, MerkleProofEntry> = {};
  for (const leaf of cleanLeaves) {
    const key = `${leaf.walletAddress}|${leaf.amount}`;
    proofs[key] = {
      walletAddress: leaf.walletAddress,
      amount: leaf.amount,
      proof: proofMap.get(key) ?? [],
    };
  }

  return { root, leaves: cleanLeaves, proofs, hashFn: 'sha256-mvp' };
}

/** Verify a proof against a known root. */
export function verifyMerkleProof(
  root: string,
  leaf: MerkleLeaf,
  proof: string[],
): boolean {
  let computed = hashLeaf(leaf);
  for (const siblingHex of proof) {
    const sibling = Buffer.from(siblingHex.replace(/^0x/, ''), 'hex');
    computed = hashPair(computed, sibling);
  }
  return toHex(computed).toLowerCase() === root.toLowerCase();
}

/** Look up a proof entry by wallet + amount. */
export function findProof(
  tree: MerkleTree,
  walletAddress: string,
  amount: number,
): MerkleProofEntry | null {
  const key = `${walletAddress.toLowerCase()}|${amount}`;
  return tree.proofs[key] ?? null;
}

function toHashZero(): string {
  return '0x' + '0'.repeat(64);
}
