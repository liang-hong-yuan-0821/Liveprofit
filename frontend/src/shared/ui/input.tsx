import type { InputHTMLAttributes } from 'react';
import { cn } from './utils';

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'flex h-9 w-full rounded-md border bg-transparent px-3 py-1 text-sm outline-none transition-colors',
        'placeholder:text-[var(--color-fg-muted)] focus-visible:border-[var(--color-accent)]',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
      {...props}
    />
  );
}
