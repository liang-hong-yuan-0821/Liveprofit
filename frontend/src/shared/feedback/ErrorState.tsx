import { Link } from 'react-router';
import { toApiError } from '../../api/client';

// 可重试/不可重试错误态：仅 retryable=true 且提供 onRetry 时显示显式重试；
// 不可重试错误只展示服务端可读摘要与可选返回入口，不展示堆栈、Token 或内部路径。
export function ErrorState({
  error,
  onRetry,
  backTo,
  backLabel,
}: {
  error: unknown;
  onRetry?: () => void;
  backTo?: string;
  backLabel?: string;
}) {
  const apiError = toApiError(error);
  const retryable = apiError.retryable;

  return (
    <div role="alert" className="py-8 text-center">
      <p className="text-sm font-medium">{retryable ? '暂时无法加载' : '无法加载'}</p>
      <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
        {apiError.message}
      </p>
      {apiError.requestId && (
        <p className="mt-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          请求标识：{apiError.requestId}
        </p>
      )}
      <div className="mt-3 flex items-center justify-center gap-3">
        {retryable && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded px-4 py-2 text-sm"
            style={{ backgroundColor: 'var(--color-accent)', color: '#fff' }}
          >
            重试
          </button>
        )}
        {!retryable && backTo && (
          <Link
            to={backTo}
            className="rounded px-4 py-2 text-sm"
            style={{ backgroundColor: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            {backLabel ?? '返回'}
          </Link>
        )}
      </div>
    </div>
  );
}
