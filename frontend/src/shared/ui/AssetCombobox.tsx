import { useEffect, useId, useRef, useState } from 'react';
import { Input } from './input';
type Asset = { ticker: string; name: string; market: string };
export function AssetCombobox({ id, assets, value, onChange }: { id: string; assets: Asset[]; value: string; onChange: (value: string) => void }) {
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();
  const [text, setText] = useState(value);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const candidates = assets.filter(asset => `${asset.ticker} ${asset.name} ${asset.market}`.toLowerCase().includes(text.trim().toLowerCase())).slice(0, 30);
  useEffect(() => { if (open && active >= 0) listRef.current?.querySelectorAll('[role=option]')[active]?.scrollIntoView?.({ block: 'nearest' }); }, [active, open]);
  function select(asset: Asset) { setSelected(asset); setText(`${asset.name} · ${asset.ticker}`); onChange(asset.ticker); setOpen(false); setActive(-1); }
  return <div className="relative">
    <Input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={listId} aria-activedescendant={open && active >= 0 && candidates[active] ? `${listId}-${active}` : undefined} autoComplete="off" placeholder="搜索名称或代码，也可直接填写资产代码" value={text}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onChange={event => { setText(event.target.value); setSelected(null); onChange(event.target.value); setActive(-1); setOpen(true); }}
      onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false); setActive(-1); }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); setActive(index => Math.max(0, Math.min(candidates.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))); }
        if (event.key === 'Enter' && open && active >= 0 && candidates[active]) { event.preventDefault(); select(candidates[active]); }
      }} />
    {open && <ul ref={listRef} role="listbox" id={listId} className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-1 shadow-xl">
      {candidates.map((asset, index) => <li key={`${asset.market}:${asset.ticker}`} id={`${listId}-${index}`} role="option" aria-selected={active === index} onMouseDown={event => event.preventDefault()} onClick={() => select(asset)} className={`cursor-pointer rounded-lg px-3 py-2 text-sm ${active === index ? 'bg-[var(--color-accent-soft)]' : 'hover:bg-[var(--color-bg)]'}`}><span>{asset.name}</span><span className="ml-2 text-xs text-[var(--color-fg-muted)]">{asset.ticker} · {asset.market}</span></li>)}
      {!candidates.length && <li className="p-3 text-xs text-[var(--color-fg-muted)]">无匹配项，将按输入内容作为资产代码提交。</li>}
    </ul>}
    <p className="mt-1.5 text-xs text-[var(--color-fg-muted)]">{selected ? `已选择 ${selected.name} · ${selected.ticker} · ${selected.market}` : '未选择候选时，将按原样提交输入的代码；名称不会自动转换为代码。'}</p>
  </div>;
}
