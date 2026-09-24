// URL-backed section navigation uses native buttons with explicit pressed state.
export function SectionTabs<T extends string>({ label, items, value, onChange }: { label: string; items: readonly { value: T; label: string }[]; value: T; onChange: (value: T) => void }) {
  return <nav aria-label={label} className="section-tabs flex flex-wrap gap-6">{items.map(item => <button key={item.value} type="button" aria-pressed={item.value === value} onClick={() => onChange(item.value)} className={`section-tab px-1 pb-3 pt-2 text-sm font-medium ${item.value === value ? '!border-[var(--color-accent-bright)] !text-[var(--color-accent-bright)]' : ''}`}>{item.label}</button>)}</nav>;
}
