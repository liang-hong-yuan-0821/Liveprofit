import { useId } from 'react';
import { Input } from './input';
import { Label } from './label';
export function percentDraft(raw: number) { return String(Number((raw * 100).toPrecision(12))); }
export function percentValue(draft: string, original: number) {
  if (draft === percentDraft(original)) return original;
  return draft.trim() === '' ? Number.NaN : Number(draft) / 100;
}
export function PercentField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = useId();
  return <div className="flex flex-col gap-1"><Label htmlFor={id}>{label}</Label><div className="relative"><Input id={id} inputMode="decimal" value={value} onChange={event => onChange(event.target.value)} className="pr-8" /><span className="absolute right-3 top-2 text-sm text-[var(--color-fg-muted)]">%</span></div></div>;
}
