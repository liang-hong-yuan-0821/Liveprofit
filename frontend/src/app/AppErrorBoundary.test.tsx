import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { AppErrorBoundary } from './AppErrorBoundary';

function Bomb(): never {
  throw new Error('render boom');
}

describe('AppErrorBoundary', () => {
  it('捕获渲染异常并显示可理解提示，不展示堆栈', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <MemoryRouter>
        <AppErrorBoundary>
          <Bomb />
        </AppErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: '页面出现未预期异常' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回大盘' })).toHaveAttribute('href', '/market');
    expect(document.body.textContent).not.toContain('render boom');
  });
});
