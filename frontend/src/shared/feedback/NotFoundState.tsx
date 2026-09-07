import { Link } from 'react-router';

// 资源不存在专用态：提供上级入口，不显示为系统崩溃；调用方不得在此建立实时连接或循环重试。
export function NotFoundState({ to, label = '返回上级页面' }: { to: string; label?: string }) {
  return (
    <div role="alert" className="py-8 text-center">
      <h1 className="text-base font-semibold">资源不存在</h1>
      <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
        请求的页面或资源不存在、或已被删除。
      </p>
      <Link
        to={to}
        className="mt-3 inline-block rounded px-4 py-2 text-sm"
        style={{ backgroundColor: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        {label}
      </Link>
    </div>
  );
}
