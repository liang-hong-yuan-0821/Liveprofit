// 通用加载占位：数据读取期间占位，不将加载误显示为无数据。
export function LoadingState({ label = '加载中…' }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" className="animate-pulse py-6 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
      {label}
    </div>
  );
}
