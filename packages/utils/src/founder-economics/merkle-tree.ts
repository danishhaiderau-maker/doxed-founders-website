/**
 * OpenZeppelin-compatible Merkle tree for Founder Economics.
 *
 * Leaf: keccak256(abi.encode(address, uint256))
 * Pair: sorted keccak256(bytes32, bytes32)
 *
 * Amounts are raw 18-decimal token units and deliberately use bigint. Do not
 * convert them through JavaScript Number: a 20M-token epoch is 2e25 units.
 */
import { AbiCoder, concat, getAddress, keccak256 } from 'ethers';

export const TOKEN_UNIT = 10n ** 18n;

export type MerkleLeaf = {
  walletAddress: string;
  amount: bigint;
};

export type MerkleProofEntry = MerkleLeaf & { proof: string[] };

export type MerkleTree = {
  root: string;
  leaves: MerkleLeaf[];
  proofs: Record<string, MerkleProofEntry>;
  hashFn: 'keccak256-abi-encode';
};

const coder = AbiCoder.defaultAbiCoder();

export function wholeTokensToUnits(amount: number): bigint {
  if (!Number.isSafeInteger(amount) || amount < 0) {
    throw new Error('Token amount must be a non-negative safe whole-token integer');
  }
  return BigInt(amount) * TOKEN_UNIT;
}

export function unitsToWholeTokens(amount: bigint): number {
  if (amount % TOKEN_UNIT !== 0n) throw new Error('Token amount is not whole-token aligned');
  const whole = amount / TOKEN_UNIT;
  if (whole > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Token amount exceeds safe display range');
  return Number(whole);
}

export function hashLeaf(leaf: MerkleLeaf): string {
  const account = getAddress(leaf.walletAddress);
  if (leaf.amount < 0n) throw new Error('Merkle leaf amount cannot be negative');
  return keccak256(coder.encode(['address', 'uint256'], [account, leaf.amount]));
}

export function hashPair(left: string, right: string): string {
  const [a, b] = left.toLowerCase() <= right.toLowerCase() ? [left, right] : [right, left];
  return keccak256(concat([a, b]));
}

function keyOf(leaf: MerkleLeaf): string {
  return `${getAddress(leaf.walletAddress).toLowerCase()}|${leaf.amount.toString()}`;
}

/** Build a deterministic, sorted-pair Merkle tree compatible with MerkleProof. */
export function buildMerkleTree(leaves: MerkleLeaf[]): MerkleTree {
  const cleanLeaves = leaves
    .map((leaf) => ({ walletAddress: getAddress(leaf.walletAddress), amount: leaf.amount }))
    .filter((leaf) => leaf.amount > 0n)
    .sort((a, b) => keyOf(a).localeCompare(keyOf(b)));

  const seenWallets = new Set<string>();
  for (const leaf of cleanLeaves) {
    const wallet = leaf.walletAddress.toLowerCase();
    if (seenWallets.has(wallet)) {
      throw new Error(`Duplicate claimant wallet in epoch: ${leaf.walletAddress}`);
    }
    seenWallets.add(wallet);
  }

  if (cleanLeaves.length === 0) throw new Error('Cannot publish an empty epoch Merkle tree');

  let level = cleanLeaves.map((leaf) => ({ hash: hashLeaf(leaf), leaves: [leaf] }));
  const proofMap = new Map<string, string[]>();
  for (const leaf of cleanLeaves) proofMap.set(keyOf(leaf), []);

  while (level.length > 1) {
    const next: typeof level = [];
    for (let i = 0; i < level.length; i += 2) {
      const left = level[i]!;
      const right = level[i + 1] ?? left;
      for (const leaf of left.leaves) proofMap.get(keyOf(leaf))?.push(right.hash);
      if (right !== left) {
        for (const leaf of right.leaves) proofMap.get(keyOf(leaf))?.push(left.hash);
      }
      // Duplicate the hash for an odd tree level, but never duplicate the
      // logical leaf membership. Duplicating `right.leaves` here would append
      // the parent sibling twice at the next level and produce an invalid
      // proof for the final unpaired claimant.
      next.push({
        hash: hashPair(left.hash, right.hash),
        leaves: right === left ? left.leaves : [...left.leaves, ...right.leaves],
      });
    }
    level = next;
  }

  const proofs: Record<string, MerkleProofEntry> = {};
  for (const leaf of cleanLeaves) {
    proofs[keyOf(leaf)] = { ...leaf, proof: proofMap.get(keyOf(leaf)) ?? [] };
  }
  return { root: level[0]!.hash, leaves: cleanLeaves, proofs, hashFn: 'keccak256-abi-encode' };
}

export function verifyMerkleProof(root: string, leaf: MerkleLeaf, proof: string[]): boolean {
  let computed = hashLeaf(leaf);
  for (const sibling of proof) computed = hashPair(computed, sibling);
  return computed.toLowerCase() === root.toLowerCase();
}

export function findProof(tree: MerkleTree, walletAddress: string, amount: bigint): MerkleProofEntry | null {
  return tree.proofs[keyOf({ walletAddress, amount })] ?? null;
}
