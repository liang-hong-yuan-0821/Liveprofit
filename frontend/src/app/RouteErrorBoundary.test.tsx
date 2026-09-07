import { render, screen } from '@testing-library/react';
import { RouterProvider, createMemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RouteErrorBoundary } from './RouteErrorBoundary';

afterEach(() => {
  vi.restoreAllMocks();
});

function renderBoundary(errorFactory: () => unknown) {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: <div>home</div>,
        errorElement: <RouteErrorBoundary />,
        loader: errorFactory,
      },
    ],
    { initialEntries: ['/'] },
  );
  render(<RouterProvider router={router} />);
}

describe('RouteErrorBoundary', () => {
  it('404 路由错误显示专用资源不存在态与返回入口，不显示为系统崩溃', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderBoundary(() => {
      throw new Response('Not Found', { status: 404 });
    });

    expect(await screen.findByRole('heading', { name: '资源不存在' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回大盘' })).toHaveAttribute('href', '/market');
  });

  it('非 404 错误显示可读摘要与返回入口，不渲染堆栈', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderBoundary(() => {
      throw new Error('上游不可用');
    });

    expect(await screen.findByRole('heading', { name: '页面加载失败' })).toBeInTheDocument();
    expect(screen.getByText('上游不可用')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回大盘' })).toHaveAttribute('href', '/market');
    expect(document.body.textContent).not.toContain('at ');
  });
});
