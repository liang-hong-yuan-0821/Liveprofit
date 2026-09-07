import type { HTMLAttributes } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from './utils';

const badgeVariants = cva('inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium', {
  variants: {
    variant: {
      default: 'border-transparent bg-[var(--color-surface)]',
      secondary: 'border-transparent bg-[var(--color-surface)] text-[var(--color-fg-muted)]',
      success: 'border-transparent bg-green-900/60 text-green-200',
      destructive: 'border-transparent bg-red-900/60 text-red-200',
      warning: 'border-transparent bg-amber-900/60 text-amber-200',
      outline: 'text-[var(--color-fg-muted)]',
    },
  },
  defaultVariants: { variant: 'default' },
});

export interface BadgeProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
