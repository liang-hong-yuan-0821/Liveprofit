import { Link, isRouteErrorResponse, useRouteError } from 'react-router';
import { NotFoundState } from '../shared/feedback/NotFoundState';

// 各一级页及任务详情的路由错误边界：
// 资源 404 → 专用 NotFoundState；其他错误 → 可读摘要，不显示堆栈。
export function RouteErrorBoundary() {
  const error = useRouteError();

  if (isRouteErrorResponse(error) && error.status === 404) {
    return (
      <main className="flex min-h-screen items-center justify-center p-6">
        <NotFoundState to="/market" label="返回大盘" />
      </main>
    );
  }

  const message = error instanceof Error ? error.message : '路由加载失败';
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div role="alert" className="max-w-md text-center">
        <h1 className="text-lg font-semibold">页面加载失败</h1>
        <p className="mt-2 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          {message}
        </p>
        <Link
          to="/market"
          className="mt-4 inline-block rounded px-4 py-2 text-sm"
          style={{ backgroundColor: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          返回大盘
        </Link>
      </div>
    </main>
  );
}
