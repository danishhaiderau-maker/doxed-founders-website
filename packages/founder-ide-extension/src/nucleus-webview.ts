/**
 * Nucleus sidebar for Founder IDE 0.9.x / Cursor / VS Code.
 * Renders Founder Graph JSON and, on click, injects a context packet into
 * @FounderOS chat (gateway alias founder-os-auto). No Void rebuild.
 */
import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import {
  authorizationHeaderFromCredentials,
  resolveCredentials,
} from './credentials';
import {
  NUCLEUS_NODE_COLORS,
  buildNucleusContextPacket,
  formatNucleusPacketVisible,
  layoutNucleusGraph,
  projectLiveNucleusGraph,
  type NucleusGraph,
  type NucleusLayout,
} from './nucleus-context';
import {
  nucleusVisibleQuery,
  setActiveNucleusPacket,
} from './nucleus-session';

type NucleusPayload = {
  graph?: NucleusGraph;
  excerpt?: string | null;
};

type WebviewMessage =
  | { type: 'refresh' }
  | { type: 'select'; nodeId?: string };

function cspToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9:*./+_-]/g, '');
}

function webviewHtml(nonce: string, cspSource: string): string {
  const colors = JSON.stringify(NUCLEUS_NODE_COLORS);
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}' ${cspSource};" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nucleus</title>
  <style>
    body { margin: 0; background: #050508; color: #e4e4e7; font: 12px/1.4 ui-sans-serif, system-ui, sans-serif; }
    header { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #27272a; }
    button { background: #6d28d9; color: white; border: 0; border-radius: 8px; padding: 4px 10px; cursor: pointer; }
    #status { color: #a1a1aa; padding: 8px 12px; }
    svg { width: 100%; height: auto; display: block; }
    #context { margin: 0 12px 12px; white-space: pre-wrap; border: 1px solid rgba(139,92,246,.35); border-radius: 10px; padding: 8px; min-height: 0; }
    #context:empty { display: none; }
  </style>
</head>
<body>
  <header>
    <strong>Nucleus</strong>
    <button id="refresh" type="button">Refresh</button>
  </header>
  <p id="status">Loading Founder Graph…</p>
  <svg id="graph" role="img" aria-label="Founder Graph"></svg>
  <pre id="context"></pre>
  <script nonce="${nonce}">
    const vscodeApi = acquireVsCodeApi();
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const COLORS = ${colors};
    const svg = document.getElementById('graph');
    const status = document.getElementById('status');
    const context = document.getElementById('context');
    let layout = null;
    let selected = null;

    function clear(el) {
      while (el.firstChild) el.removeChild(el.firstChild);
    }

    function colorFor(type) {
      return Object.prototype.hasOwnProperty.call(COLORS, type) ? COLORS[type] : '#a1a1aa';
    }

    function draw() {
      clear(svg);
      if (!layout || !Array.isArray(layout.nodes)) return;
      svg.setAttribute('viewBox', '0 0 ' + layout.width + ' ' + layout.height);
      const pos = new Map(layout.nodes.map((node) => [node.id, node]));
      for (const edge of layout.edges || []) {
        const from = pos.get(edge.from);
        const to = pos.get(edge.to);
        if (!from || !to) continue;
        const line = document.createElementNS(SVG_NS, 'line');
        line.setAttribute('x1', String(from.x));
        line.setAttribute('y1', String(from.y));
        line.setAttribute('x2', String(to.x));
        line.setAttribute('y2', String(to.y));
        line.setAttribute('stroke', '#3f3f46');
        line.setAttribute('stroke-width', '1.25');
        const title = document.createElementNS(SVG_NS, 'title');
        title.textContent = edge.rel || '';
        line.appendChild(title);
        svg.appendChild(line);
      }
      for (const node of layout.nodes) {
        const group = document.createElementNS(SVG_NS, 'g');
        group.setAttribute('data-node-id', node.id);
        group.setAttribute('data-selected', node.id === selected ? 'true' : 'false');
        group.style.cursor = 'pointer';
        const circle = document.createElementNS(SVG_NS, 'circle');
        circle.setAttribute('cx', String(node.x));
        circle.setAttribute('cy', String(node.y));
        circle.setAttribute('r', node.id === selected ? '18' : '16');
        circle.setAttribute('fill', colorFor(node.type));
        circle.setAttribute('stroke', node.id === selected ? '#fafafa' : 'transparent');
        circle.setAttribute('stroke-width', node.id === selected ? '3' : '0');
        const text = document.createElementNS(SVG_NS, 'text');
        text.setAttribute('x', String(node.x));
        text.setAttribute('y', String(node.y + 34));
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('fill', '#e4e4e7');
        text.setAttribute('font-size', '11');
        const label = typeof node.label === 'string' ? node.label : node.id;
        text.textContent = label.length > 28 ? label.slice(0, 26) + '…' : label;
        group.appendChild(circle);
        group.appendChild(text);
        group.addEventListener('click', () => {
          selected = node.id;
          vscodeApi.postMessage({ type: 'select', nodeId: node.id });
          draw();
        });
        svg.appendChild(group);
      }
    }

    window.addEventListener('message', (event) => {
      const msg = event.data || {};
      if (msg.type === 'graph') {
        layout = msg.layout;
        status.textContent = msg.error || (layout && layout.nodes && layout.nodes.length ? layout.nodes.length + ' nodes' : 'No Founder Graph nodes yet.');
        draw();
      } else if (msg.type === 'selected') {
        selected = msg.nodeId || null;
        context.textContent = msg.visible || '';
        draw();
      }
    });

    document.getElementById('refresh').addEventListener('click', () => {
      vscodeApi.postMessage({ type: 'refresh' });
    });
  </script>
</body>
</html>`;
}

async function openFounderOsChat(query: string): Promise<void> {
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', { query });
    return;
  } catch {
    /* older hosts take a string */
  }
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', query);
  } catch {
    void vscode.window.showInformationMessage(
      'Nucleus context is attached. Open Chat and ask @FounderOS — the selected node stays in the system prompt until you pick another.',
    );
  }
}

export class NucleusViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewId = 'founderOs.nucleus';
  private view: vscode.WebviewView | undefined;
  private listeners: vscode.Disposable[] = [];
  private graph: NucleusGraph | null = null;

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.listeners.forEach((item) => item.dispose());
    const nonce = randomBytes(16).toString('hex');
    view.webview.options = { enableScripts: true };
    view.webview.html = webviewHtml(nonce, cspToken(view.webview.cspSource));
    this.listeners = [
      view.webview.onDidReceiveMessage((message: WebviewMessage) => {
        if (message?.type === 'refresh') void this.refresh();
        else if (message?.type === 'select' && message.nodeId) void this.select(message.nodeId);
      }),
      view.onDidChangeVisibility(() => {
        if (view.visible && !this.graph) void this.refresh();
      }),
    ];
    void this.refresh();
  }

  private post(message: {
    type: 'graph' | 'selected';
    layout?: NucleusLayout;
    error?: string;
    nodeId?: string;
    visible?: string;
  }): void {
    void this.view?.webview.postMessage(message);
  }

  async refresh(): Promise<void> {
    const creds = resolveCredentials();
    if (!creds) {
      this.graph = null;
      this.post({
        type: 'graph',
        layout: layoutNucleusGraph(null),
        error:
          'Pair Founder Node to load Nucleus. The extension sends Authorization: FounderNode {nodeId}:{token} to GET /api/ide/nucleus.',
      });
      return;
    }
    try {
      const url = `${creds.apiBaseUrl.replace(/\/$/, '')}/api/ide/nucleus`;
      const res = await fetch(url, {
        headers: {
          Accept: 'application/json',
          Authorization: authorizationHeaderFromCredentials(creds),
        },
      });
      if (!res.ok) {
        this.post({
          type: 'graph',
          layout: layoutNucleusGraph(this.graph),
          error: `Founder Graph request failed (${res.status}).`,
        });
        return;
      }
      const body = (await res.json()) as NucleusPayload & { liveGraph?: NucleusGraph };
      this.graph = projectLiveNucleusGraph(body.liveGraph ?? body.graph ?? null);
      this.post({ type: 'graph', layout: layoutNucleusGraph(this.graph) });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not load Nucleus.';
      this.post({
        type: 'graph',
        layout: layoutNucleusGraph(this.graph),
        error: message,
      });
    }
  }

  private async select(nodeId: string): Promise<void> {
    if (!this.graph) return;
    const packet = buildNucleusContextPacket(projectLiveNucleusGraph(this.graph), nodeId);
    if (!packet) return;
    setActiveNucleusPacket(packet);
    this.post({
      type: 'selected',
      nodeId: packet.nodeId,
      visible: formatNucleusPacketVisible(packet),
    });
    await openFounderOsChat(nucleusVisibleQuery(packet));
  }
}
