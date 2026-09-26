import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  NUCLEUS_GATEWAY_MODEL,
  buildNucleusContextPacket,
  buildNucleusGatewayMessages,
  type NucleusGraph,
} from '@dcf/utils';
import { NucleusMap } from './nucleus-map';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

const graph: NucleusGraph = {
  nodes: [
    { id: 'initiative:ship', type: 'initiative', label: 'Ship Nucleus' },
    {
      id: 'task:xss',
      type: 'task',
      label: '<script>alert(1)</script>',
      detail: 'detail',
    },
  ],
  edges: [{ from: 'initiative:ship', to: 'task:xss', rel: 'contains' }],
  focusInitiativeId: 'initiative:ship',
};

test('Nucleus map renders Founder Graph nodes and escapes labels', () => {
  const html = renderToStaticMarkup(
    React.createElement(NucleusMap, {
      graph,
      selectedId: 'task:xss',
      onSelect: () => undefined,
    }),
  );
  assert.match(html, /Ship Nucleus/);
  assert.match(html, /data-selected="true"/);
  assert.match(html, /data-node-id="task:xss"/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
});

test('selecting a node injects its label into the founder-os-auto prompt', () => {
  const packet = buildNucleusContextPacket(graph, 'task:xss');
  assert.ok(packet);
  const messages = buildNucleusGatewayMessages({
    packet,
    userText: 'What should change?',
  });
  assert.equal(NUCLEUS_GATEWAY_MODEL, 'founder-os-auto');
  assert.match(messages[0].content, /label: <script>alert\(1\)<\/script>/);
  assert.match(messages[0].content, /task:xss/);
  assert.equal(messages[0].content.split('</nucleus-context>').length - 1, 1);
  assert.equal(messages[1].content, 'What should change?');
  const page = readFileSync(new URL('../../app/founder-ide/nucleus/page.tsx', import.meta.url), 'utf8');
  const panel = readFileSync(new URL('./nucleus-panel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /phone-completions/);
  assert.match(panel, /buildNucleusContextPacket/);
  assert.match(page, /NucleusPanel/);
  assert.doesNotMatch(panel, /localStorage/);
});
