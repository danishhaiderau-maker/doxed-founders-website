import fs from 'node:fs';
import { FOUNDER_VAULT_FILES, vaultFilePath, type FounderVaultFileKey } from './paths.js';

const INDEXABLE_KEYS: FounderVaultFileKey[] = [
  'projectContext',
  'roadmap',
  'tasks',
  'decisions',
];

export type VaultVectorChunk = {
  id: string;
  source: string;
  text: string;
  terms: Record<string, number>;
};

export type VaultVectorIndex = {
  version: 1;
  updatedAt: string;
  chunks: VaultVectorChunk[];
};

export type VaultSearchHit = {
  id: string;
  source: string;
  text: string;
  score: number;
};

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((t) => t.length > 2);
}

function termFreq(tokens: string[]): Record<string, number> {
  const freq: Record<string, number> = {};
  for (const token of tokens) {
    freq[token] = (freq[token] ?? 0) + 1;
  }
  return freq;
}

function chunkText(text: string, maxLen = 420): string[] {
  const normalized = text.replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];
  if (normalized.length <= maxLen) return [normalized];

  const chunks: string[] = [];
  const paragraphs = normalized.split(/\n{2,}/);
  let buffer = '';

  for (const paragraph of paragraphs) {
    const next = buffer ? `${buffer}\n\n${paragraph}` : paragraph;
    if (next.length <= maxLen) {
      buffer = next;
      continue;
    }
    if (buffer) chunks.push(buffer);
    if (paragraph.length <= maxLen) {
      buffer = paragraph;
      continue;
    }
    for (let i = 0; i < paragraph.length; i += maxLen) {
      chunks.push(paragraph.slice(i, i + maxLen));
    }
    buffer = '';
  }
  if (buffer) chunks.push(buffer);
  return chunks;
}

function readVaultSources(vaultRoot: string): { source: string; text: string }[] {
  const sources: { source: string; text: string }[] = [];
  for (const key of INDEXABLE_KEYS) {
    const filePath = vaultFilePath(vaultRoot, key);
    if (!fs.existsSync(filePath)) continue;
    const text = fs.readFileSync(filePath, 'utf8').trim();
    if (!text) continue;
    sources.push({ source: FOUNDER_VAULT_FILES[key], text });
  }
  return sources;
}

export function buildVaultVectorIndex(vaultRoot: string): VaultVectorIndex {
  const chunks: VaultVectorChunk[] = [];
  let chunkId = 0;

  for (const { source, text } of readVaultSources(vaultRoot)) {
    for (const piece of chunkText(text)) {
      const tokens = tokenize(piece);
      if (tokens.length === 0) continue;
      chunks.push({
        id: `chunk_${chunkId++}`,
        source,
        text: piece,
        terms: termFreq(tokens),
      });
    }
  }

  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    chunks,
  };
}

export function writeVaultVectorIndex(vaultRoot: string, index: VaultVectorIndex): void {
  fs.writeFileSync(
    vaultFilePath(vaultRoot, 'vectorIndex'),
    JSON.stringify(index, null, 2),
    'utf8',
  );
}

export function readVaultVectorIndex(vaultRoot: string): VaultVectorIndex | null {
  const filePath = vaultFilePath(vaultRoot, 'vectorIndex');
  if (!fs.existsSync(filePath)) return null;
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8')) as VaultVectorIndex;
    if (parsed?.version !== 1 || !Array.isArray(parsed.chunks)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function rebuildVaultVectorIndex(vaultRoot: string): VaultVectorIndex {
  const index = buildVaultVectorIndex(vaultRoot);
  writeVaultVectorIndex(vaultRoot, index);
  return index;
}

function cosineSimilarity(a: Record<string, number>, b: Record<string, number>): number {
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (const [term, weight] of Object.entries(a)) {
    normA += weight * weight;
    if (b[term]) dot += weight * b[term];
  }
  for (const weight of Object.values(b)) {
    normB += weight * weight;
  }
  if (normA === 0 || normB === 0) return 0;
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

export function searchVaultVectorIndex(
  index: VaultVectorIndex,
  query: string,
  topK = 5,
): VaultSearchHit[] {
  const queryTerms = termFreq(tokenize(query));
  if (Object.keys(queryTerms).length === 0) return [];

  return index.chunks
    .map((chunk) => ({
      id: chunk.id,
      source: chunk.source,
      text: chunk.text,
      score: cosineSimilarity(queryTerms, chunk.terms),
    }))
    .filter((hit) => hit.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);
}

export function searchVaultOnDisk(
  vaultRoot: string,
  query: string,
  topK = 5,
): VaultSearchHit[] {
  const index = readVaultVectorIndex(vaultRoot) ?? rebuildVaultVectorIndex(vaultRoot);
  return searchVaultVectorIndex(index, query, topK);
}
