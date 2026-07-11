"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.CostTracker = void 0;
/**
 * Session DDollar spend tracker + status-bar display.
 *
 * Accumulates per-request DDollar cost across a session (resets on editor
 * restart — fine for Phase 4). The chat provider forwards the `founderOs` SSE
 * metadata line (which includes `ddollarCost`, `tier`, `provider`, `model`) to
 * `record()`, which updates:
 *   - a status-bar item with a coin icon showing the running session total
 *   - a tooltip with the last request's tier/provider/model/cost and a
 *     breakdown of the last few requests.
 *
 * See design report §5.3 / §8.2 and the `founderOs` metadata emitter in
 * `apps/api/src/ai-proxy/ai-proxy.controller.ts`.
 */
const vscode = __importStar(require("vscode"));
const MAX_HISTORY = 12;
class CostTracker {
    bar;
    sessionTotal = 0;
    history = [];
    lastEvent;
    constructor() {
        this.bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 98);
        this.bar.command = 'founderOs.showCostBreakdown';
    }
    /** Record a cost event from the gateway's `founderOs` metadata line. */
    record(meta) {
        const evt = { ...meta, at: Date.now() };
        this.lastEvent = evt;
        this.history.unshift(evt);
        if (this.history.length > MAX_HISTORY)
            this.history.length = MAX_HISTORY;
        if (typeof evt.ddollarCost === 'number') {
            this.sessionTotal += evt.ddollarCost;
        }
        this.refresh();
    }
    /** Reset the session total (e.g. via a command). */
    reset() {
        this.sessionTotal = 0;
        this.history = [];
        this.lastEvent = undefined;
        this.refresh();
    }
    show() {
        this.refresh();
        this.bar.show();
    }
    refresh() {
        const total = this.sessionTotal;
        if (total > 0) {
            this.bar.text = `$(credit-card) ${total} D$`;
        }
        else {
            this.bar.text = `$(credit-card) — D$`;
        }
        this.bar.tooltip = this.buildTooltip();
    }
    buildTooltip() {
        const md = new vscode.MarkdownString(undefined, true);
        md.isTrusted = false;
        md.supportHtml = true;
        md.appendMarkdown(`**Founder OS — DDollar spend**\n\n`);
        md.appendMarkdown(`Session total: **${this.sessionTotal} D$**\n\n`);
        if (this.lastEvent) {
            const l = this.lastEvent;
            md.appendMarkdown(`Last request:\n\n`);
            md.appendMarkdown(`- Tier: \`${l.tier ?? '?'}\`\n`);
            md.appendMarkdown(`- Provider: \`${l.provider ?? '?'}\`\n`);
            md.appendMarkdown(`- Model: \`${l.model ?? '?'}\`\n`);
            md.appendMarkdown(`- Cost: \`${typeof l.ddollarCost === 'number' ? `${l.ddollarCost} D$` : 'n/a'}\`\n\n`);
        }
        if (this.history.length > 0) {
            md.appendMarkdown(`Recent requests:\n\n`);
            md.appendMarkdown(`| Time | Tier | Provider | Cost |\n`);
            md.appendMarkdown(`|---|---|---|---|\n`);
            for (const e of this.history.slice(0, MAX_HISTORY)) {
                const time = new Date(e.at).toLocaleTimeString();
                md.appendMarkdown(`| ${time} | ${e.tier ?? '?'} | ${e.provider ?? '?'} | ${typeof e.ddollarCost === 'number' ? `${e.ddollarCost}` : '—'} |\n`);
            }
        }
        else {
            md.appendMarkdown(`_No cost events yet this session._`);
        }
        return md;
    }
    /** Show a human-readable breakdown in an information message. */
    async showBreakdown() {
        const lines = [`Founder OS — DDollar session total: ${this.sessionTotal} D$`];
        if (this.lastEvent) {
            lines.push(`Last: tier=${this.lastEvent.tier ?? '?'}, provider=${this.lastEvent.provider ?? '?'}, model=${this.lastEvent.model ?? '?'}, cost=${this.lastEvent.ddollarCost ?? 'n/a'}`);
        }
        if (this.history.length > 0) {
            lines.push(`Recent (${Math.min(this.history.length, MAX_HISTORY)}):`);
            for (const e of this.history.slice(0, MAX_HISTORY)) {
                lines.push(`  ${new Date(e.at).toLocaleTimeString()} — ${e.tier ?? '?'}/${e.provider ?? '?'} — ${e.ddollarCost ?? '—'} D$`);
            }
        }
        void vscode.window.showInformationMessage(lines.join('\n'), { modal: false });
    }
    dispose() {
        this.bar.dispose();
    }
}
exports.CostTracker = CostTracker;
//# sourceMappingURL=cost-tracker.js.map