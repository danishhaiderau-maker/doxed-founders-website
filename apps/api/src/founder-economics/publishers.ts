/**
 * Settlement publishers — on-chain Merkle root + IPFS proof data.
 *
 * Real paths are env-gated. Without credentials we persist a clearly-marked
 * offline receipt so demos and local settle still work. With credentials we
 * call the configured RPC / IPFS HTTP API.
 *
 * Env:
 *   FOUNDER_ECONOMICS_RPC_URL              — JSON-RPC endpoint (optional)
 *   FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS  — EpochDistributor contract (optional)
 *   FOUNDER_ECONOMICS_PUBLISH_PRIVATE_KEY  — keeper key for publishRoot (optional)
 *   FOUNDER_ECONOMICS_IPFS_API_URL         — e.g. https://ipfs.infura.io:5001 (optional)
 *   FOUNDER_ECONOMICS_IPFS_AUTH            — Basic auth header value (optional)
 */

export type MerklePublishResult = {
  txHash: string;
  mode: 'on-chain' | 'offline-receipt';
  detail: string;
};

export type ProofDataPublishResult = {
  uri: string;
  mode: 'ipfs' | 'offline-receipt';
  detail: string;
};

export interface MerkleRootPublisher {
  publish(epochNumber: number, root: string, totalTokens: number): Promise<MerklePublishResult>;
}

export interface ProofDataPublisher {
  publish(epochNumber: number, payload: unknown): Promise<ProofDataPublishResult>;
}

/** Persist a deterministic offline receipt when chain credentials are absent. */
export class OfflineMerkleRootPublisher implements MerkleRootPublisher {
  async publish(epochNumber: number, root: string, _totalTokens: number): Promise<MerklePublishResult> {
    const short = root.replace(/^0x/i, '').slice(0, 40);
    return {
      txHash: `offline-0x${short || Buffer.from(String(epochNumber)).toString('hex').padStart(40, '0')}`,
      mode: 'offline-receipt',
      detail:
        'No FOUNDER_ECONOMICS_RPC_URL + FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS + FOUNDER_ECONOMICS_PUBLISH_PRIVATE_KEY — stored offline receipt. Wire those env vars to call EpochDistributor.publishRoot.',
    };
  }
}

/**
 * Best-effort JSON-RPC eth_sendRawTransaction path.
 * Requires a pre-signed raw tx via FOUNDER_ECONOMICS_SIGNED_PUBLISH_TX, or
 * falls back to eth_call simulation + offline receipt when only RPC+address
 * are set (Solidity ABI encoding of publishRoot needs a wallet SDK).
 */
export class EnvGatedOnChainMerklePublisher implements MerkleRootPublisher {
  private readonly offline = new OfflineMerkleRootPublisher();

  async publish(epochNumber: number, root: string, totalTokens: number): Promise<MerklePublishResult> {
    const rpcUrl = process.env.FOUNDER_ECONOMICS_RPC_URL?.trim();
    const distributor = process.env.FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS?.trim();
    const signedTx = process.env.FOUNDER_ECONOMICS_SIGNED_PUBLISH_TX?.trim();
    const privateKey = process.env.FOUNDER_ECONOMICS_PUBLISH_PRIVATE_KEY?.trim();

    if (!rpcUrl || !distributor) {
      return this.offline.publish(epochNumber, root, totalTokens);
    }

    // Preferred: operator supplies a pre-signed publishRoot tx for this epoch.
    if (signedTx) {
      try {
        const res = await fetch(rpcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            jsonrpc: '2.0',
            id: 1,
            method: 'eth_sendRawTransaction',
            params: [signedTx.startsWith('0x') ? signedTx : `0x${signedTx}`],
          }),
          signal: AbortSignal.timeout(30_000),
        });
        const body = (await res.json()) as { result?: string; error?: { message?: string } };
        if (body.result) {
          return {
            txHash: body.result,
            mode: 'on-chain',
            detail: `Published via eth_sendRawTransaction to ${distributor}`,
          };
        }
        return {
          ...(await this.offline.publish(epochNumber, root, totalTokens)),
          detail: `RPC rejected signed tx: ${body.error?.message ?? 'unknown'} — offline receipt stored`,
        };
      } catch (err) {
        return {
          ...(await this.offline.publish(epochNumber, root, totalTokens)),
          detail: `RPC publish failed: ${err instanceof Error ? err.message : String(err)} — offline receipt stored`,
        };
      }
    }

    // RPC + distributor present but no signed tx / wallet SDK — verify the
    // node is reachable, then store an offline receipt with the intended root.
    try {
      const res = await fetch(rpcUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_blockNumber', params: [] }),
        signal: AbortSignal.timeout(10_000),
      });
      const body = (await res.json()) as { result?: string };
      const block = body.result ?? 'unknown';
      const receipt = await this.offline.publish(epochNumber, root, totalTokens);
      return {
        ...receipt,
        detail:
          `RPC reachable (block=${block}) at ${distributor}; privateKey=${privateKey ? 'set' : 'unset'}. ` +
          `Provide FOUNDER_ECONOMICS_SIGNED_PUBLISH_TX (or a Solidity toolchain keeper) to submit publishRoot(${epochNumber}, ${root.slice(0, 18)}…). Offline receipt stored.`,
      };
    } catch (err) {
      return {
        ...(await this.offline.publish(epochNumber, root, totalTokens)),
        detail: `RPC unreachable: ${err instanceof Error ? err.message : String(err)} — offline receipt stored`,
      };
    }
  }
}

export class OfflineProofDataPublisher implements ProofDataPublisher {
  async publish(epochNumber: number, payload: unknown): Promise<ProofDataPublishResult> {
    const hash = Buffer.from(JSON.stringify(payload)).toString('hex').slice(0, 32);
    return {
      uri: `offline://epoch-${epochNumber}-${hash || Date.now()}`,
      mode: 'offline-receipt',
      detail:
        'No FOUNDER_ECONOMICS_IPFS_API_URL — stored offline proof URI. Set IPFS API URL (+ optional FOUNDER_ECONOMICS_IPFS_AUTH) to pin settlement JSON.',
    };
  }
}

/** POST /api/v0/add against a Kubo/Infura-compatible IPFS HTTP API when configured. */
export class EnvGatedIpfsProofPublisher implements ProofDataPublisher {
  private readonly offline = new OfflineProofDataPublisher();

  async publish(epochNumber: number, payload: unknown): Promise<ProofDataPublishResult> {
    const apiUrl = process.env.FOUNDER_ECONOMICS_IPFS_API_URL?.trim();
    if (!apiUrl) {
      return this.offline.publish(epochNumber, payload);
    }

    try {
      const body = JSON.stringify({
        epochNumber,
        publishedAt: new Date().toISOString(),
        payload,
      });
      const form = new FormData();
      form.append('file', new Blob([body], { type: 'application/json' }), `epoch-${epochNumber}.json`);

      const headers: Record<string, string> = {};
      const auth = process.env.FOUNDER_ECONOMICS_IPFS_AUTH?.trim();
      if (auth) headers.Authorization = auth;

      const endpoint = apiUrl.replace(/\/$/, '') + '/api/v0/add?pin=true';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers,
        body: form,
        signal: AbortSignal.timeout(30_000),
      });
      if (!res.ok) {
        const text = await res.text();
        return {
          ...(await this.offline.publish(epochNumber, payload)),
          detail: `IPFS add failed (${res.status}): ${text.slice(0, 120)} — offline receipt stored`,
        };
      }
      const json = (await res.json()) as { Hash?: string; cid?: string };
      const cid = json.Hash ?? json.cid;
      if (!cid) {
        return {
          ...(await this.offline.publish(epochNumber, payload)),
          detail: 'IPFS add returned no CID — offline receipt stored',
        };
      }
      return {
        uri: `ipfs://${cid}`,
        mode: 'ipfs',
        detail: `Pinned settlement proof to IPFS (${cid})`,
      };
    } catch (err) {
      return {
        ...(await this.offline.publish(epochNumber, payload)),
        detail: `IPFS publish failed: ${err instanceof Error ? err.message : String(err)} — offline receipt stored`,
      };
    }
  }
}

export function createDefaultPublishers(): {
  merkle: MerkleRootPublisher;
  proofData: ProofDataPublisher;
} {
  return {
    merkle: new EnvGatedOnChainMerklePublisher(),
    proofData: new EnvGatedIpfsProofPublisher(),
  };
}
