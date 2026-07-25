import path from 'node:path';
import { FounderCodeIntelligenceIndex } from './code-intelligence-index';

const SUPPORTED_PROTOCOL_VERSIONS = new Set([
  '2025-11-25',
  '2025-06-18',
  '2025-03-26',
  '2024-11-05',
]);
const LATEST_PROTOCOL_VERSION = '2025-11-25';

interface JsonRpcRequest {
  jsonrpc: '2.0';
  id?: string | number;
  method: string;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

interface ToolDefinition {
  name: string;
  title: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

const TOOLS: ToolDefinition[] = [
  {
    name: 'founder_code_map',
    title: 'Founder code map',
    description:
      'Return a local graph-ranked repository map and bounded file/line tuples for a task. Source remains on the device.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', minLength: 1, maxLength: 4_000 },
        activeFile: { type: 'string', maxLength: 1_000 },
        limit: { type: 'integer', minimum: 1, maximum: 100 },
        maxEstimatedTokens: { type: 'integer', minimum: 256, maximum: 4_000 },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'founder_dependency_impact',
    title: 'Founder dependency impact',
    description:
      'Show direct imports and reverse dependents for a workspace-relative file before editing it.',
    inputSchema: {
      type: 'object',
      properties: {
        file: { type: 'string', minLength: 1, maxLength: 1_000 },
      },
      required: ['file'],
      additionalProperties: false,
    },
  },
  {
    name: 'founder_refresh_code_index',
    title: 'Refresh Founder code index',
    description:
      'Refresh the local repository index. Unchanged files reuse persisted hashes; force performs a full local reindex.',
    inputSchema: {
      type: 'object',
      properties: {
        force: { type: 'boolean' },
      },
      additionalProperties: false,
    },
  },
];

export class FounderCodeIntelligenceMcpProtocol {
  private readonly index: FounderCodeIntelligenceIndex;

  constructor(workspaceRoot: string) {
    this.index = new FounderCodeIntelligenceIndex(workspaceRoot);
  }

  async handle(message: unknown): Promise<JsonRpcResponse | null> {
    if (!isJsonRpcRequest(message)) {
      return rpcError(null, -32600, 'Invalid JSON-RPC request.');
    }
    if (message.id === undefined) {
      return null;
    }
    try {
      switch (message.method) {
        case 'initialize': {
          const requested = stringValue(message.params?.protocolVersion);
          const protocolVersion = requested && SUPPORTED_PROTOCOL_VERSIONS.has(requested)
            ? requested
            : LATEST_PROTOCOL_VERSION;
          return rpcResult(message.id, {
            protocolVersion,
            capabilities: { tools: { listChanged: false } },
            serverInfo: {
              name: 'founder-code-intelligence',
              version: '1.0.0',
            },
            instructions:
              'Call founder_code_map before reading many files. Read only returned ranges and refresh after edits.',
          });
        }
        case 'ping':
          return rpcResult(message.id, {});
        case 'tools/list':
          return rpcResult(message.id, { tools: TOOLS });
        case 'tools/call':
          return this.callTool(message.id, message.params);
        default:
          return rpcError(message.id, -32601, `Method not found: ${message.method}`);
      }
    } catch (error) {
      return rpcError(
        message.id,
        -32603,
        error instanceof Error ? error.message : 'Founder code intelligence failed.',
      );
    }
  }

  private async callTool(
    id: string | number,
    params: Record<string, unknown> | undefined,
  ): Promise<JsonRpcResponse> {
    const name = stringValue(params?.name);
    const args = objectValue(params?.arguments);
    if (!name) return rpcError(id, -32602, 'Tool name is required.');
    if (name === 'founder_code_map') {
      const query = boundedString(args.query, 'query', 1, 4_000);
      const activeFile = optionalBoundedString(args.activeFile, 'activeFile', 1_000);
      const limit = optionalInteger(args.limit, 'limit', 1, 100);
      const maxEstimatedTokens = optionalInteger(
        args.maxEstimatedTokens,
        'maxEstimatedTokens',
        256,
        4_000,
      );
      const result = await this.index.query({
        query,
        activeFile,
        limit,
        maxEstimatedTokens,
      });
      return toolResult(id, {
        workspaceId: result.workspaceId,
        indexedAt: result.indexedAt,
        files: result.files,
        symbols: result.symbols,
        refreshedFiles: result.refreshedFiles,
        reusedFiles: result.reusedFiles,
        persistence: result.persistence,
        tuples: result.tuples,
        promptMap: result.promptMap,
      });
    }
    if (name === 'founder_dependency_impact') {
      const file = boundedString(args.file, 'file', 1, 1_000);
      return toolResult(id, { file, ...(await this.index.impact(file)) });
    }
    if (name === 'founder_refresh_code_index') {
      if (args.force !== undefined && typeof args.force !== 'boolean') {
        return rpcError(id, -32602, 'force must be a boolean.');
      }
      return toolResult(id, await this.index.refresh(Boolean(args.force)));
    }
    return rpcError(id, -32602, `Unknown tool: ${name}`);
  }
}

export async function startCodeIntelligenceMcp(
  workspaceRoot = workspaceArgument(process.argv.slice(2)),
): Promise<void> {
  const protocol = new FounderCodeIntelligenceMcpProtocol(workspaceRoot);
  let buffered = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk: string) => {
    buffered += chunk;
    let newline = buffered.indexOf('\n');
    while (newline >= 0) {
      const line = buffered.slice(0, newline).replace(/\r$/, '');
      buffered = buffered.slice(newline + 1);
      if (line.trim()) {
        void respond(protocol, line);
      }
      newline = buffered.indexOf('\n');
    }
  });
  process.stdin.resume();
}

async function respond(
  protocol: FounderCodeIntelligenceMcpProtocol,
  line: string,
): Promise<void> {
  let input: unknown;
  try {
    input = JSON.parse(line);
  } catch {
    process.stdout.write(`${JSON.stringify(rpcError(null, -32700, 'Parse error.'))}\n`);
    return;
  }
  const response = await protocol.handle(input);
  if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
}

function workspaceArgument(argv: string[]): string {
  const marker = argv.indexOf('--workspace');
  const value = marker >= 0 ? argv[marker + 1] : undefined;
  return path.resolve(value || process.cwd());
}

function toolResult(id: string | number, value: unknown): JsonRpcResponse {
  return rpcResult(id, {
    content: [{ type: 'text', text: JSON.stringify(value) }],
    structuredContent: value,
  });
}

function rpcResult(id: string | number, result: unknown): JsonRpcResponse {
  return { jsonrpc: '2.0', id, result };
}

function rpcError(
  id: string | number | null,
  code: number,
  message: string,
  data?: unknown,
): JsonRpcResponse {
  return {
    jsonrpc: '2.0',
    id,
    error: { code, message, ...(data === undefined ? {} : { data }) },
  };
}

function isJsonRpcRequest(value: unknown): value is JsonRpcRequest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const request = value as Partial<JsonRpcRequest>;
  return request.jsonrpc === '2.0' && typeof request.method === 'string';
}

function objectValue(value: unknown): Record<string, unknown> {
  if (value === undefined) return {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Tool arguments must be an object.');
  }
  return value as Record<string, unknown>;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function boundedString(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): string {
  if (typeof value !== 'string' || value.length < minimum || value.length > maximum) {
    throw new Error(`${label} must be ${minimum}-${maximum} characters.`);
  }
  return value;
}

function optionalBoundedString(
  value: unknown,
  label: string,
  maximum: number,
): string | undefined {
  return value === undefined
    ? undefined
    : boundedString(value, label, 1, maximum);
}

function optionalInteger(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): number | undefined {
  if (value === undefined) return undefined;
  if (!Number.isInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new Error(`${label} must be an integer between ${minimum} and ${maximum}.`);
  }
  return Number(value);
}

if (require.main === module) {
  startCodeIntelligenceMcp().catch((error: unknown) => {
    process.stderr.write(
      `[founder-code-intelligence] ${error instanceof Error ? error.message : String(error)}\n`,
    );
    process.exitCode = 1;
  });
}
