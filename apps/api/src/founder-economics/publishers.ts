/**
 * Production-safe settlement publishers.
 *
 * A root is never marked published from an offline receipt, a static signed
 * transaction, or an unpinned payload. The only supported operational path is
 * Robinhood EVM testnet (46630) -> pin immutable proof data -> keeper proposes
 * the exact root -> receipt and contract state are verified.
 */

import {
  Contract,
  Interface,
  JsonRpcProvider,
  Wallet,
  type Log,
  type LogDescription,
  getAddress,
  keccak256,
  toUtf8Bytes,
} from 'ethers';

export const ROBINHOOD_EVM_TESTNET_CHAIN_ID = 46_630;

const DISTRIBUTOR_ABI = [
  'function proposeRoot(uint256 epoch, bytes32 root, uint256 totalAllocated, bytes32 modelCodeHash, bytes32 proofDataHash)',
  'function epochs(uint256 epoch) view returns (uint256 funded, uint256 totalClaimed, bytes32 root, bytes32 modelCodeHash, bytes32 proofDataHash, uint64 proposedAt, uint64 finalizedAt, uint8 status)',
  'event RootProposed(uint256 indexed epoch, bytes32 root, bytes32 modelCodeHash, bytes32 proofDataHash)',
];

export type RootProposal = {
  epochNumber: number;
  root: string;
  totalAllocatedRaw: bigint;
  modelCodeHash: string;
  proofDataHash: string;
};

export type MerklePublishResult = {
  txHash: string;
  blockNumber: number;
  challengeEndsAt: Date;
  mode: 'on-chain';
  detail: string;
};

export type ProofDataPublishResult = {
  uri: string;
  contentHash: string;
  mode: 'ipfs';
  detail: string;
};

export interface MerkleRootPublisher {
  propose(input: RootProposal): Promise<MerklePublishResult>;
}

export interface ProofDataPublisher {
  publish(epochNumber: number, payload: unknown): Promise<ProofDataPublishResult>;
}

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for a testnet epoch settlement`);
  return value;
}

/** Canonical JSON so the IPFS payload hash is reproducible during an audit. */
export function stableJson(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') {
    return JSON.stringify(value);
  }
  if (typeof value === 'bigint') return JSON.stringify(value.toString());
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('Settlement payload contains a non-finite number');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((entry) => stableJson(entry)).join(',')}]`;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(',')}}`;
  }
  throw new Error(`Unsupported settlement payload value: ${typeof value}`);
}

export function hashProofPayload(payload: unknown): string {
  return keccak256(toUtf8Bytes(stableJson(payload)));
}

/**
 * Posts a keeper-signed `proposeRoot` transaction and verifies both its log and
 * the distributor's stored state. A transaction hash by itself is not proof of
 * a valid root proposal.
 */
export class RobinhoodTestnetMerklePublisher implements MerkleRootPublisher {
  async propose(input: RootProposal): Promise<MerklePublishResult> {
    if (!Number.isSafeInteger(input.epochNumber) || input.epochNumber <= 0) {
      throw new Error('Epoch number must be a positive safe integer');
    }
    if (input.totalAllocatedRaw <= 0n) throw new Error('Root allocation must be positive');

    const rpcUrl = requiredEnvironment('FOUNDER_ECONOMICS_RPC_URL');
    const distributorAddress = getAddress(requiredEnvironment('FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS'));
    const privateKey = requiredEnvironment('FOUNDER_ECONOMICS_PUBLISH_PRIVATE_KEY');
    const provider = new JsonRpcProvider(rpcUrl);
    const network = await provider.getNetwork();
    if (Number(network.chainId) !== ROBINHOOD_EVM_TESTNET_CHAIN_ID) {
      throw new Error(
        `Refusing settlement on chain ${network.chainId}; Robinhood EVM testnet ${ROBINHOOD_EVM_TESTNET_CHAIN_ID} is required`,
      );
    }

    const signer = new Wallet(privateKey, provider);
    const distributor = new Contract(distributorAddress, DISTRIBUTOR_ABI, signer);
    const tx = await distributor.proposeRoot(
      BigInt(input.epochNumber),
      input.root,
      input.totalAllocatedRaw,
      input.modelCodeHash,
      input.proofDataHash,
    );
    const receipt = await tx.wait();
    if (!receipt || receipt.status !== 1) {
      throw new Error(`Root proposal transaction failed for epoch ${input.epochNumber}`);
    }

    const eventInterface = new Interface(DISTRIBUTOR_ABI);
    const event = receipt.logs
      .map((log: Log): LogDescription | null => {
        try {
          return eventInterface.parseLog(log);
        } catch {
          return null;
        }
      })
      .find((log: LogDescription | null) => log?.name === 'RootProposed');
    if (!event
      || BigInt(event.args.epoch) !== BigInt(input.epochNumber)
      || String(event.args.root).toLowerCase() !== input.root.toLowerCase()
      || String(event.args.modelCodeHash).toLowerCase() !== input.modelCodeHash.toLowerCase()
      || String(event.args.proofDataHash).toLowerCase() !== input.proofDataHash.toLowerCase()) {
      throw new Error(`RootProposed event verification failed for epoch ${input.epochNumber}`);
    }

    const stored = await distributor.epochs(BigInt(input.epochNumber));
    if (BigInt(stored.funded) !== input.totalAllocatedRaw
      || String(stored.root).toLowerCase() !== input.root.toLowerCase()
      || String(stored.modelCodeHash).toLowerCase() !== input.modelCodeHash.toLowerCase()
      || String(stored.proofDataHash).toLowerCase() !== input.proofDataHash.toLowerCase()
      || Number(stored.status) !== 2) {
      throw new Error(`Distributor state verification failed for epoch ${input.epochNumber}`);
    }

    return {
      txHash: receipt.hash,
      blockNumber: receipt.blockNumber,
      challengeEndsAt: new Date(Number(stored.proposedAt) * 1000 + 7 * 24 * 60 * 60 * 1000),
      mode: 'on-chain',
      detail: `Verified RootProposed on Robinhood EVM testnet for epoch ${input.epochNumber}`,
    };
  }
}

/** Pins the exact canonical proof payload and returns the content keccak hash. */
export class RequiredIpfsProofPublisher implements ProofDataPublisher {
  async publish(epochNumber: number, payload: unknown): Promise<ProofDataPublishResult> {
    const apiUrl = requiredEnvironment('FOUNDER_ECONOMICS_IPFS_API_URL');
    const content = stableJson({ epochNumber, payload });
    const contentHash = keccak256(toUtf8Bytes(content));
    const form = new FormData();
    form.append('file', new Blob([content], { type: 'application/json' }), `epoch-${epochNumber}.json`);

    const headers: Record<string, string> = {};
    const auth = process.env.FOUNDER_ECONOMICS_IPFS_AUTH?.trim();
    if (auth) headers.Authorization = auth;
    const endpoint = `${apiUrl.replace(/\/$/, '')}/api/v0/add?pin=true`;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: form,
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      throw new Error(`IPFS pin failed (${response.status}): ${(await response.text()).slice(0, 240)}`);
    }
    const json = (await response.json()) as { Hash?: string; cid?: string };
    const cid = json.Hash ?? json.cid;
    if (!cid) throw new Error('IPFS pin response did not include a CID');
    return {
      uri: `ipfs://${cid}`,
      contentHash,
      mode: 'ipfs',
      detail: `Pinned immutable settlement proof to IPFS (${cid})`,
    };
  }
}

export function createDefaultPublishers(): {
  merkle: MerkleRootPublisher;
  proofData: ProofDataPublisher;
} {
  return {
    merkle: new RobinhoodTestnetMerklePublisher(),
    proofData: new RequiredIpfsProofPublisher(),
  };
}
