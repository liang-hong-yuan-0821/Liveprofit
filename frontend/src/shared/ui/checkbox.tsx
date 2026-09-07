import type { InputHTMLAttributes } from 'react';
import { cn } from './utils';

// 简单受控复选框（原生 input + 样式），供层级/选项多选使用
export function Checkbox({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      type="checkbox"
      className={cn('size-4 shrink-0 accent-[var(--color-accent)] disabled:cursor-not-allowed disabled:opacity-50', className)}
      {...props}
    />
  );
}
