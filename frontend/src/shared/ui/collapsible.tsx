import type { ComponentProps } from 'react';
import * as CollapsiblePrimitive from '@radix-ui/react-collapsible';
import { ChevronRight } from 'lucide-react';
import { cn } from './utils';

// shadcn 风格封装 @radix-ui/react-collapsible（模式同 dialog.tsx）：
// 单例受控折叠（open/onOpenChange），适合日志面板多层相互独立的嵌套展开。
const Collapsible = CollapsiblePrimitive.Root;
const CollapsibleTrigger = CollapsiblePrimitive.Trigger;

function CollapsibleHeader({ className, children, ...props }: ComponentProps<typeof CollapsiblePrimitive.Trigger>) {
  return (
    <CollapsiblePrimitive.Trigger
      className={cn(
        'group flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-sm font-medium',
        'hover:bg-[var(--color-bg-muted)] focus-visible:outline-none focus-visible:ring-1',
        className,
      )}
      {...props}
    >
      <ChevronRight className="size-4 shrink-0 transition-transform duration-200 group-data-[state=open]:rotate-90" />
      {children}
    </CollapsiblePrimitive.Trigger>
  );
}

function CollapsibleContent({ className, ...props }: ComponentProps<typeof CollapsiblePrimitive.Content>) {
  return (
    <CollapsiblePrimitive.Content
      className={cn(
        'overflow-hidden pl-5 data-[state=closed]:hidden',
        className,
      )}
      {...props}
    />
  );
}

export { Collapsible, CollapsibleContent, CollapsibleHeader, CollapsibleTrigger };
