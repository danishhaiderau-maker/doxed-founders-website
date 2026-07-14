/**
 * Merkle tree builder + verifier for the Founder Economics MVP.
 *
 * Leaf shape mirrors the on-chain `EpochDistributor.claim` exactly:
 *
 *   leaf = keccak256(abi.encode(address, amount))
 *
 * where `abi.encode` is STANDARD (non-packed) ABI encoding:
 *   - address  → 32 bytes (left-padded to 20)
 *   - uint256  → 32 bytes (big-endian)
 *
 * Pair hashing uses the OpenZeppelin MerkleProof convention:
 *
 *   parent = keccak256(sorted(left, right))
 *
 * The off-chain tree layout (sorted pairs, duplicate-last-leaf) already
 * matches OpenZeppelin's `MerkleProof.verify`, so a proof generated here
 * verifies on-chain against `MerkleProof.verify(proof, root, leaf)`.
 *
 * Uses `ethers` v6 (`keccak256`, `AbiCoder`, `toUtf8Bytes` are safe to
 * import from `@dcf/utils` in Next.js client bundles — ethers ships an
 * isomorphic keccak that does not require `node:crypto`).
 */

import { AbiCoder, keccak256, getBytes } from 'ethers';

export type MerkleLeaf = {
  walletAddress: string;
  amount: bigint;
};

export type MerkleProofEntry = {
  walletAddress: string;
  amount: bigint;
  /** Ordered list of sibling hashes from leaf to root (0x-prefixed hex). */
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
  hashFn: 'keccak256';
};

/** Single abi.encode(address, uint256) coder — reused for every leaf. */
const LEAF_ABI_CODER = new AbiCoder();

/**
 * Encode a leaf exactly like Solidity `abi.encode(account, amount)`.
 *
 * Solidity `abi.encode` pads each argument to a full 32-byte word:
 *   - address → right-aligned in 32 bytes (left-padded with zeros)
 *   - uint256 → big-endian 32 bytes
 *
 * We pass `amount` as a bigint so values up to 2^256-1 round-trip without
 * the 53-bit precision loss of a JS number (20M × 10^18 overflows Number).
 */
export function encodeLeaf(leaf: MerkleLeaf): Uint8Array {
  return getBytes(
    LEAF_ABI_CODER.encode(
      ['address', 'uint256'],
      [leaf.walletAddress, leaf.amount],
    ),
  );
}

/**
 * Hash a single leaf — `keccak256(abi.encode(address, amount))`.
 *
 * Byte-identical to the on-chain `EpochDistributor.claim`:
 *
 *   bytes32 leaf = keccak256(abi.encode(account, amount));
 */
export function hashLeaf(leaf: MerkleLeaf): Uint8Array {
  return getBytes(keccak256(encodeLeaf(leaf)));
}

/**
 * Hash two child nodes into their parent. Sorted — smaller hash goes first.
 *
 * This mirrors OpenZeppelin's `MerkleProof.processProof`, which expects
 * the sibling order to be canonical (sorted) so verification is symmetric.
 */
export function hashPair(left: Uint8Array, right: Uint8Array): Uint8Array {
  const [a, b] = compareBytes(left, right) <= 0 ? [left, right] : [right, left];
  return getBytes(keccak256(concatBytes(a, b)));
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
    const key = `${l.walletAddress.toLowerCase()}|${l.amount.toString()}`;
    deduped.set(key, { walletAddress: l.walletAddress.toLowerCase(), amount: l.amount });
  }
  const cleanLeaves = Array.from(deduped.values());

  if (cleanLeaves.length === 0) {
    return {
      root: ZERO_HASH_32,
      leaves: [],
      proofs: {},
      hashFn: 'keccak256',
    };
  }

  let level: { hash: Uint8Array; leaf: MerkleLeaf }[] = cleanLeaves.map((leaf) => ({
    hash: hashLeaf(leaf),
    leaf,
  }));

  const proofMap = new Map<string, string[]>();
  for (const { leaf } of level) {
    proofMap.set(`${leaf.walletAddress}|${leaf.amount.toString()}`, []);
  }

  while (level.length > 1) {
    const next: { hash: Uint8Array; leaf: MerkleLeaf }[] = [];
    for (let i = 0; i < level.length; i += 2) {
      const left = level[i]!;
      const right = i + 1 < level.length ? level[i + 1]! : left;
      const parentHash = hashPair(left.hash, right.hash);

      for (const entry of level.slice(i, i + 2)) {
        const key = `${entry.leaf.walletAddress}|${entry.leaf.amount.toString()}`;
        const sibling = entry === left ? right : left;
        proofMap.get(key)?.push(toHex(sibling.hash));
      }

      next.push({ hash: parentHash, leaf: left.leaf });
    }
    level = next;
  }

  const root = toHex(level[0]!.hash);
  const proofs: Record<string, MerkleProofEntry> = {};
  for (const leaf of cleanLeaves) {
    const key = `${leaf.walletAddress}|${leaf.amount.toString()}`;
    proofs[key] = {
      walletAddress: leaf.walletAddress,
      amount: leaf.amount,
      proof: proofMap.get(key) ?? [],
    };
  }

  return { root, leaves: cleanLeaves, proofs, hashFn: 'keccak256' };
}

/** Verify a proof against a known root using the same Keccak-256 pair hash. */
export function verifyMerkleProof(
  root: string,
  leaf: MerkleLeaf,
  proof: string[],
): boolean {
  let computed = hashLeaf(leaf);
  for (const siblingHex of proof) {
    const sibling = fromHex(siblingHex);
    computed = hashPair(computed, sibling);
  }
  return toHex(computed).toLowerCase() === root.toLowerCase();
}

/** Look up a proof entry by wallet + amount. */
export function findProof(
  tree: MerkleTree,
  walletAddress: string,
  amount: bigint,
): MerkleProofEntry | null {
  const key = `${walletAddress.toLowerCase()}|${amount.toString()}`;
  return tree.proofs[key] ?? null;
}

// ─── internal byte helpers (no Node Buffer — browser-safe) ────────────────

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    if (a[i] !== b[i]) return a[i]! - b[i]!;
  }
  return a.length - b.length;
}

function toHex(buf: Uint8Array): string {
  let s = '0x';
  for (let i = 0; i < buf.length; i++) s += buf[i]!.toString(16).padStart(2, '0');
  return s;
}

function fromHex(hex: string): Uint8Array {
  const clean = hex.replace(/^0x/, '');
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

/** 32-byte zero hash — root sentinel for an empty tree. */
const ZERO_HASH_32 = '0x' + '0'.repeat(64);
