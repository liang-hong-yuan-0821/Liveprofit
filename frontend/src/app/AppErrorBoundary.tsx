import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Link } from 'react-router';

interface Props {
  // errorElement 场景不传 children；仅包裹子树的场景传 children
  children?: ReactNode;
}

interface State {
  error: Error | null;
}

// 根路由错误边界：捕获渲染异常并给出可理解提示，不展示堆栈或内部细节。
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 仅记录到控制台用于排障；页面不向用户展示堆栈
    console.error('[AppErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="flex min-h-screen items-center justify-center p-6">
          <div role="alert" className="max-w-md text-center">
            <h1 className="text-lg font-semibold">页面出现未预期异常</h1>
            <p className="mt-2 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
              已捕获渲染异常，未影响本地数据。可返回大盘继续使用。
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
    return this.props.children;
  }
}
