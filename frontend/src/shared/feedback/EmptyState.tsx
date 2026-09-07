import type { ReactNode } from 'react';

// 正常业务空态：无数据说明与可选操作入口，不作为系统错误展示。
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="py-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          {description}
        </p>
      )}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
