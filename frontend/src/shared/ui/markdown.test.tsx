import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MarkdownView } from './markdown';

describe('MarkdownView', () => {
  it('# 标题渲染为 heading 元素', () => {
    render(<MarkdownView content="# 标题" />);
    expect(screen.getByRole('heading', { name: '标题' })).toBeInTheDocument();
  });

  it('GFM 表格渲染为表格（单元格文本可见）', () => {
    render(<MarkdownView content={'| 排名 | 行业 |\n|------|------|\n| 1 | 石油石化 |'} />);
    const table = screen.getByRole('table');
    expect(table).toBeInTheDocument();
    expect(screen.getByText('石油石化')).toBeInTheDocument();
  });

  it('单换行渲染为 <br>（breaks 语义）', () => {
    const { container } = render(<MarkdownView content={'第一行\n第二行'} />);
    expect(container.querySelector('br')).not.toBeNull();
  });

  it('raw HTML 转义为文本呈现（不生成真实元素）', () => {
    const { container } = render(<MarkdownView content={'<script>alert(1)</script>'} />);
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
  });

  it('空串渲染无异常', () => {
    const { container } = render(<MarkdownView content="" />);
    expect(container).not.toBeNull();
  });
});
