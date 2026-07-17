/**
 * Gateway metadata UI surfaces (Phase 4 Task 2).
 *
 * Surfaces tier / provider / model / request-id / DDollar-cost in three
 * places, all driven from the `founderOs` SSE metadata line that the gateway
 * emits before each chat completion (design report §5.3 / §8.2):
 *
 *   1. Status bar item — last request's tier + cost
 *      e.g. `FOUNDER OS · reasoning · $0.012`
 *   2. Output channel — "Founder OS" — redacted request/response metadata
 *      for advanced users debugging routing decisions.
 *   3. Markdown hover/footer builder — provider + model + request-id, used
 *      by the chat provider to annotate the message footer.
 *
 * All output passes through `redactSecrets()` so the output channel never
 * leaks `Authorization` headers, `nodeToken`, or `api_key` values.
 */
import * as vscode from 'vscode';
import { redactSecrets, type GatewayFounderOsMetadata } from './gateway-client';

export interface GatewayMetaEvent extends GatewayFounderOsMetadata {
  /** ISO timestamp the metadata was received. */
  at: string;
}

const MAX_HISTORY = 50;

export class GatewayMetadataUi {
  /** Status bar — last request tier + DDollar cost. */
  private readonly bar: vscode.StatusBarItem;
  /** Output channel — redacted request/response log. */
  private readonly channel: vscode.OutputChannel;
  private history: GatewayMetaEvent[] = [];
  private lastEvent: GatewayMetaEvent | undefined;

  constructor() {
    this.bar = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      97,
    );
    this.bar.command = 'founderOs.showGatewayMetadata';
    this.bar.tooltip = 'Founder OS — last gateway request metadata. Click to open the output channel.';
    this.channel = vscode.window.createOutputChannel('Founder OS', { log: true });
  }

  /** Record a metadata event from the gateway SSE pre-line. */
  record(meta: GatewayFounderOsMetadata): void {
    const evt: GatewayMetaEvent = { ...meta, at: new Date().toISOString() };
    this.lastEvent = evt;
    this.history.unshift(evt);
    if (this.history.length > MAX_HISTORY) this.history.length = MAX_HISTORY;
    this.refreshBar();
    this.writeToChannel(evt);
  }

  /** Reset state (e.g. on connection re-pair). */
  reset(): void {
    this.history = [];
    this.lastEvent = undefined;
    this.refreshBar();
  }

  show(): void {
    this.refreshBar();
    this.bar.show();
  }

  dispose(): void {
    this.bar.dispose();
    this.channel.dispose();
  }

  /** Reveal the output channel — wired to a command. */
  revealChannel(): void {
    this.channel.show(true);
  }

  /**
   * Build a markdown footer (or hover body) for a chat message annotating
   * provider + model + request-id. Used by the chat provider so users can
   * copy the request-id when filing bug reports.
   */
  buildFooterMarkdown(meta: GatewayFounderOsMetadata | undefined): vscode.MarkdownString {
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = false;
    md.supportHtml = false;
    if (!meta) {
      md.appendMarkdown('_Founder OS — no routing metadata received for this request._');
      return md;
    }
    const tier = meta.tier ?? '?';
    const provider = meta.provider ?? '?';
    const model = meta.model ?? '?';
    const reqId = meta.requestId ?? '—';
    const cost = typeof meta.ddollarCost === 'number' ? `${meta.ddollarCost.toFixed(4)} D$` : 'n/a';
    md.appendMarkdown(`---\n\n`);
    md.appendMarkdown(
      `Founder OS — tier: \`${tier}\` · provider: \`${provider}\` · model: \`${model}\` · cost: \`${cost}\` · request-id: \`${reqId}\``,
    );
    return md;
  }

  private refreshBar(): void {
    if (!this.lastEvent) {
      this.bar.text = '$(sparkle) FOUNDER OS';
      return;
    }
    const e = this.lastEvent;
    const tier = e.tier ?? '?';
    const cost =
      typeof e.ddollarCost === 'number' ? `$${e.ddollarCost.toFixed(4)}` : '';
    this.bar.text = `$(sparkle) FOUNDER OS · ${tier}${cost ? ` · ${cost}` : ''}`;
    const provider = e.provider ?? '?';
    const model = e.model ?? '?';
    const reqId = e.requestId ?? '—';
    this.bar.tooltip = `Last gateway route — tier: ${tier}, provider: ${provider}, model: ${model}, cost: ${cost || 'n/a'}, request-id: ${reqId}. Click to open output channel.`;
  }

  private writeToChannel(evt: GatewayMetaEvent): void {
    const lines = [
      `[${evt.at}] gateway metadata:`,
      `  requestId: ${redactSecrets(evt.requestId ?? '—')}`,
      `  tier: ${redactSecrets(evt.tier ?? '—')}`,
      `  provider: ${redactSecrets(evt.provider ?? '—')}`,
      `  model: ${redactSecrets(evt.model ?? '—')}`,
      `  ddollarCost: ${typeof evt.ddollarCost === 'number' ? evt.ddollarCost.toFixed(6) : '—'}`,
    ];
    for (const line of lines) {
      this.channel.appendLine(line);
    }
  }

  /** Show a QuickPick of recent metadata events (debug aid). */
  async showRecent(): Promise<void> {
    if (this.history.length === 0) {
      void vscode.window.showInformationMessage(
        'Founder OS: no gateway metadata yet this session.',
      );
      return;
    }
    const items = this.history.slice(0, 12).map((e) => ({
      label: `${e.tier ?? '?'} · ${e.provider ?? '?'} · ${e.model ?? '?'}`,
      description: typeof e.ddollarCost === 'number' ? `${e.ddollarCost.toFixed(4)} D$` : '',
      detail: `request-id: ${e.requestId ?? '—'} · ${e.at}`,
    }));
    const picked = await vscode.window.showQuickPick(items, {
      placeHolder: 'Founder OS — recent gateway requests',
    });
    if (picked) this.revealChannel();
  }
}
