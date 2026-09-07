import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithRouter } from '../../../../test/utils';
import { ReportContent } from './ReportContent';
import { toReportViewModel } from './reportMappers/toReportViewModels';

function makeReport() {
  return {
    schema_version: 'v1',
    report_version: 1,
    generated_at: '2026-09-05T09:05:00Z',
    task: { task_id: 't-1', task_type: 'SINGLE_STOCK', ticker: '000001.SZ', effective_trade_date: null, duration_ms: 1000 },
    sections: [
      { block: 'market', status: 'AVAILABLE', title: '市场环境', summary: null, content: `${'首'.repeat(130)}\n\n第二段内容很长。`, charts: null, unavailable_reason: null, retryable: null },
      { block: 'sector', status: 'UNAVAILABLE', title: '板块分析', summary: null, content: null, charts: null, unavailable_reason: '数据源失败', retryable: true },
      { block: 'stock', status: 'NOT_REQUESTED', title: '个股研究', summary: null, content: null, charts: null, unavailable_reason: null, retryable: null },
      { block: 'decision', status: 'AVAILABLE', title: '交易决策', summary: '决策摘要', content: '决策正文', charts: null, unavailable_reason: null, retryable: null },
    ],
    data_sources: [{ label: '行情', source: 'tushare', as_of: '2026-09-04' }],
    risk_note: '风险说明',
  };
}

describe('ReportContent', () => {
  it('decision 默认展开（正文可见）；market 默认折叠（无 summary 用正文首段截断预览）', () => {
    renderWithRouter(<ReportContent report={toReportViewModel(makeReport() as never)} />);

    expect(screen.getByText('决策正文')).toBeInTheDocument();
    // market 折叠：第二段不可见，首段以截断预览展示
    expect(screen.queryByText(/第二段内容很长/)).not.toBeInTheDocument();
    expect(screen.getByText(/首{100}/)).toBeInTheDocument();
  });

  it('展开/收起切换：market 展开全文后正文可见', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ReportContent report={toReportViewModel(makeReport() as never)} />);

    const expandButtons = screen.getAllByRole('button', { name: '展开全文' });
    await user.click(expandButtons[0]);
    expect(screen.getByText(/首{130}/)).toBeInTheDocument();
    expect(screen.getByText(/第二段内容很长/)).toBeInTheDocument();
  });

  it('AVAILABLE 分区正文按 markdown 渲染（标题渲染为 heading）', () => {
    const base = makeReport();
    const report = {
      ...base,
      sections: base.sections.map((s, i) => (i === 3 ? { ...s, content: '# 决策标题\n\n决策正文' } : s)),
    };
    renderWithRouter(<ReportContent report={toReportViewModel(report as never)} />);

    expect(screen.getByRole('heading', { name: '决策标题' })).toBeInTheDocument();
    expect(screen.getByText('决策正文')).toBeInTheDocument();
  });

  it('UNAVAILABLE：显示服务端原因，可重试时手动重新读取；其他区块继续显示', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderWithRouter(<ReportContent report={toReportViewModel(makeReport() as never)} onRetryReport={onRetry} />);

    expect(screen.getByText('数据源失败')).toBeInTheDocument();
    expect(screen.getByText('决策正文')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重新读取报告' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('NOT_REQUESTED：显示说明并引导新建包含该层级的任务', () => {
    renderWithRouter(<ReportContent report={toReportViewModel(makeReport() as never)} />);

    expect(screen.getByText('本次分析未请求此区块。')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '新建包含该层级的分析' })).toHaveAttribute('href', '/ai?create=1');
  });

  it('数据来源与风险说明展示', () => {
    renderWithRouter(<ReportContent report={toReportViewModel(makeReport() as never)} />);

    expect(screen.getByText(/行情：tushare/)).toBeInTheDocument();
    expect(screen.getByText('风险说明')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /版本切换/ })).not.toBeInTheDocument();
  });
});
