/**
 * In-memory Nucleus selection. Never written to globalState, secrets, or
 * browser storage — the packet is labels and ids from the Founder Graph.
 */
import type { GatewayMessage } from './gateway-client';
import {
  escapeNucleusHtml,
  formatNucleusPacketChatFence,
  formatNucleusPacketForPrompt,
  formatNucleusPacketVisible,
  type NucleusContextPacket,
} from './nucleus-context';

let active: NucleusContextPacket | null = null;

export function setActiveNucleusPacket(packet: NucleusContextPacket | null): void {
  active = packet;
}

export function getActiveNucleusPacket(): NucleusContextPacket | null {
  return active;
}

/** Prepend the selected node to the gateway system prompt (same slot as memory). */
export function withNucleusSystem(messages: GatewayMessage[]): GatewayMessage[] {
  if (!active) return messages;
  const block = formatNucleusPacketForPrompt(active);
  const first = messages[0];
  if (first && first.role === 'system') {
    return [{ ...first, content: `${first.content}\n\n${block}` }, ...messages.slice(1)];
  }
  return [{ role: 'system', content: block }, ...messages];
}

/**
 * Visible chat banner when the user message does not already cite the node.
 * HTML-escaped and fenced so a label cannot break out of the chat renderer.
 */
export function nucleusChatPreface(userPrompt: string): string | null {
  if (!active) return null;
  if (userPrompt.includes(active.nodeId)) return null;
  return formatNucleusPacketChatFence(active);
}

/** Query passed to the built-in chat view so the node context is on screen. */
export function nucleusVisibleQuery(packet: NucleusContextPacket): string {
  return `@FounderOS\n${escapeNucleusHtml(formatNucleusPacketVisible(packet))}\n\nWhat should change?`;
}
