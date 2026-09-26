'use client';

import {
  layoutNucleusGraph,
  nucleusNodeColor,
  type NucleusGraph,
} from '@dcf/utils';

type Props = {
  graph: NucleusGraph;
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
};

function shortLabel(label: string): string {
  return label.length > 28 ? `${label.slice(0, 26)}…` : label;
}

/**
 * SVG map of a Founder Graph. Labels are React text nodes, so a node label
 * cannot inject HTML. This is not the economics KnowledgeGraphViz list.
 */
export function NucleusMap({ graph, selectedId, onSelect }: Props) {
  const layout = layoutNucleusGraph(graph);
  const placed = new Map(layout.nodes.map((node) => [node.id, node]));

  return (
    <svg
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      role="img"
      aria-label="Founder Graph"
      className="h-auto w-full"
    >
      {layout.edges.map((edge) => {
        const from = placed.get(edge.from);
        const to = placed.get(edge.to);
        if (!from || !to) return null;
        return (
          <line
            key={`${edge.from}-${edge.to}-${edge.rel}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke="#3f3f46"
            strokeWidth={1.25}
          >
            <title>{edge.rel}</title>
          </line>
        );
      })}
      {layout.nodes.map((node) => {
        const selected = node.id === selectedId;
        return (
          <g
            key={node.id}
            role="button"
            tabIndex={0}
            data-node-id={node.id}
            data-selected={selected ? 'true' : 'false'}
            aria-pressed={selected}
            aria-label={`${node.type}: ${node.label}`}
            className="cursor-pointer outline-none"
            onClick={() => onSelect(node.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(node.id);
              }
            }}
          >
            <circle
              cx={node.x}
              cy={node.y}
              r={selected ? 18 : 16}
              fill={nucleusNodeColor(node.type)}
              stroke={selected ? '#fafafa' : 'transparent'}
              strokeWidth={selected ? 3 : 0}
            />
            <text
              x={node.x}
              y={node.y + 34}
              textAnchor="middle"
              fill="#e4e4e7"
              fontSize={11}
            >
              {shortLabel(node.label)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
