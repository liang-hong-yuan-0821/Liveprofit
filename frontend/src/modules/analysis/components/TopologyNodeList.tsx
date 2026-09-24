import { useState } from 'react';
import { TOPOLOGY_LAYER_LABELS } from './topologyChartOption';
export function TopologyNodeList({ nodes, onSelect }: { nodes: Array<{ id: string; label: string; layer?: string; has_prompt?: boolean; status?: string }>; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const layers = [...new Set(nodes.map(node => node.layer ?? node.id.split(':')[0]))];
  return <details onToggle={event => setOpen(event.currentTarget.open)} className="mt-3 rounded-xl border border-[var(--color-border)] p-3"><summary className="text-sm">按层查看全部节点</summary>{open && <div className="mt-3 space-y-4">{layers.map(layer => <section key={layer}><h3 className="mb-2 text-xs font-semibold text-[var(--color-fg-muted)]">{TOPOLOGY_LAYER_LABELS[layer] ?? layer}</h3><div className="flex flex-wrap gap-2">{nodes.filter(node => (node.layer ?? node.id.split(':')[0]) === layer).map(node => <button key={node.id} type="button" disabled={node.has_prompt === false} onClick={() => onSelect(node.id)} className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-left text-xs hover:border-[var(--color-accent)] disabled:opacity-60">{node.label}{node.has_prompt === false ? ' · 纯代码，只读' : ''}</button>)}</div></section>)}</div>}</details>;
}
