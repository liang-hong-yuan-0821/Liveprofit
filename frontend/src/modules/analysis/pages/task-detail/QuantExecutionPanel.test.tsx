// QuantExecutionPanel 组件测试（plan 4.4.3）：买点/持仓/订单三态、拒绝码展示、
// 「需人工确认，未下单」标注、空态与告警。

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { QuantExecutionDTO } from '../../../../api/generated';
import { QuantExecutionPanel } from './QuantExecutionPanel';
import { renderWithRouter } from '../../../../test/utils';

vi.mock('../../../../api/generated/services/QuantSignalsService', () => ({
  QuantSignalsService: { listQuantSignalsApiV1AnalysisTasksTaskIdQuantSignalsGet: vi.fn() },
}));

import { QuantSignalsService } from '../../../../api/generated/services/QuantSignalsService';

const signalsMock = QuantSignalsService.listQuantSignalsApiV1AnalysisTasksTaskIdQuantSignalsGet as ReturnType<typeof vi.fn>;

function signalsEnvelope(rows: object[]) {
  return {
    data: {
      items: rows.map((r, i) => ({ id: i + 1, ts_code: '', signal_kind: 'BUY', attempt_no: 1, ...r })),
      next_cursor: null,
      attempt_no: 1,
      kind: 'buy',
    },
    meta: { request_id: 'req-1', schema_version: 'v1' },
  };
}

beforeEach(() => {
  signalsMock.mockReset();
  signalsMock.mockResolvedValue(signalsEnvelope([]));
});

function makeQuant(overrides: Partial<QuantExecutionDTO> = {}): QuantExecutionDTO {
  return {
    strategy: { name: '双均线', version_no: 2, source_hash_prefix: 'abcdef123456', published_at: null },
    portfolio_snapshot: {
      name: '核心仓',
      version: 1,
      total_assets: '100000.0000',
      available_cash: '35000.0000',
      risk: {},
      snapshot_at: null,
    },
    matching_buy_preview: [],
    holding_signal_preview: [],
    suggested_order_preview: [],
    summary: {
      universe_total: 5300,
      data_complete: 5200,
      scanned: 5200,
      buy_matches: 3,
      suggested_buy_orders: 2,
      suggested_sell_orders: 1,
      failed_count: 1,
    },
    warnings: ['风险门控未启用（AI 层后续接入）'],
    valued_at: '2026-09-16T14:00:00+00:00',
    ...overrides,
  };
}

describe('QuantExecutionPanel', () => {
  it('渲染扫描统计与告警', () => {
    renderWithRouter(<QuantExecutionPanel taskId="task-1" quant={makeQuant()} />);
    expect(screen.getByLabelText('量化执行')).toBeInTheDocument();
    expect(screen.getByText('5300')).toBeInTheDocument();
    expect(screen.getByText(/风险门控未启用（AI 层后续接入）/)).toBeInTheDocument();
  });

  it('买点列表展示全部 BUY（含被风控拒绝的），拒绝码可见', async () => {
    signalsMock.mockResolvedValue(
      signalsEnvelope([
        { ts_code: '000001.SZ', score: 90, reason: '金叉', entry_price: 10, stop_loss: 9, take_profit: 12, order_status: 'ELIGIBLE' },
        { ts_code: '000002.SZ', score: 80, reason: '超卖', entry_price: 8, stop_loss: 7, take_profit: 10, order_status: 'BUY_REJECTED_CASH' },
      ]),
    );
    renderWithRouter(
      <QuantExecutionPanel
        taskId="task-1"
        quant={makeQuant()}
      />,
    );
    expect(await screen.findByText('000001.SZ')).toBeInTheDocument();
    expect(screen.getByText('000002.SZ')).toBeInTheDocument();
    expect(screen.getByText('ELIGIBLE')).toBeInTheDocument();
    expect(screen.getByText('BUY_REJECTED_CASH')).toBeInTheDocument();
  });

  it('建议订单页标注「需人工确认，未下单」，空订单渲染空态', async () => {
    const user = userEvent.setup();
    signalsMock.mockImplementation((_taskId: string, kind: string) =>
      Promise.resolve(
        kind === 'orders'
          ? signalsEnvelope([
              { ts_code: '000001.SZ', action: 'BUY', shares: 1000, notional: 10000, order_cost_price: 10, valuation_price: 10, stop_loss: 9, take_profit: 12, risk_bucket: { source: 'SW2021', industry_code: '801080', industry_name: '电子' } },
            ])
          : signalsEnvelope([]),
      ),
    );
    renderWithRouter(
      <QuantExecutionPanel
        taskId="task-1"
        quant={makeQuant()}
      />,
    );
    await user.click(screen.getByRole('button', { name: '建议订单' }));
    expect(screen.getByText(/需人工确认，未下单/)).toBeInTheDocument();
    expect(await screen.findByText(/电子/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '买点' }));
    expect(screen.getByText('无数据')).toBeInTheDocument();
  });
});
