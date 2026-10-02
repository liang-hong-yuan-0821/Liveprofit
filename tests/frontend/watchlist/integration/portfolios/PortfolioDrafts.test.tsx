// test-catalog-begin
// {
//   "purpose": "自选与组合界面 / Frozen editing snapshots through real parent query boundaries：cached background error leaves the settings dialog and draft mounted；parent list refresh preserves settings draft and blocks stale save until explicit reload；saving an invalid collapsed field expands its group and focuses the input",
//   "keywords": [
//     "自选与组合界面",
//     "自选组合",
//     "执行准入",
//     "投资组合",
//     "仓位管理",
//     "数量",
//     "行情刷新",
//     "版本修订",
//     "portfolio_drafts",
//     "admission",
//     "portfolio",
//     "position",
//     "quantity",
//     "refresh",
//     "revision"
//   ],
//   "covers": [
//     "frontend/src/api/client.ts",
//     "frontend/src/api/generated/services/PortfoliosService.ts",
//     "frontend/src/api/queryKeys.ts",
//     "frontend/src/modules/watchlist/portfolios/PortfolioList.tsx",
//     "frontend/src/modules/watchlist/portfolios/PositionList.tsx"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithRouter } from '../../../../support/frontend/utils';
import { queryKeys } from '../../../../../frontend/src/api/queryKeys';
import { ApiError } from '../../../../../frontend/src/api/client';
import { PortfolioList } from '../../../../../frontend/src/modules/watchlist/portfolios/PortfolioList';
import { PositionList } from '../../../../../frontend/src/modules/watchlist/portfolios/PositionList';
vi.mock('../../../../../frontend/src/api/generated/services/PortfoliosService', () => ({ PortfoliosService: {
  listPortfoliosApiV1PortfoliosGet: vi.fn(), listPositionsApiV1PortfoliosPortfolioIdPositionsGet: vi.fn(),
  updatePortfolioApiV1PortfoliosPortfolioIdPatch: vi.fn(), upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut: vi.fn(),
} }));
import { PortfoliosService as api } from '../../../../../frontend/src/api/generated/services/PortfoliosService';
const portfolio = { id: 'p1', name: '测试组合', version: 1, position_count: 1, total_assets: 100000, available_cash: 80000,
  risk_per_trade_pct: .0123456789012345, min_risk_reward_ratio: 2, max_total_position_pct: .8, max_single_stock_pct: .2, max_sector_pct: .4,
  max_portfolio_open_risk_pct: .06, max_sector_open_risk_pct: .03, max_daily_new_risk_pct: .02, max_drawdown_pct: .2, max_daily_loss_pct: .03,
  net_asset_value: null, peak_net_asset_value: null, day_start_net_asset_value: null, risk_facts_as_of: null, created_at: '', updated_at: '' };
const envelope = (data: unknown) => ({ data, meta: {} });
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listPortfoliosApiV1PortfoliosGet).mockResolvedValue(envelope({ items: [portfolio] }) as never);
  vi.mocked(api.listPositionsApiV1PortfoliosPortfolioIdPositionsGet).mockResolvedValue(envelope({ portfolio_revision: 4, items: [] }) as never);
});
describe('Frozen editing snapshots through real parent query boundaries', () => {
  it('cached background error leaves the settings dialog and draft mounted', async () => {
    const user = userEvent.setup(); const { queryClient } = renderWithRouter(<PortfolioList selectedId="p1" onSelect={() => {}} />);
    await user.click(await screen.findByRole('button', { name: '组合设置' }));
    const name = screen.getByLabelText('名称'); await user.clear(name); await user.type(name, '刷新失败也保留');
    vi.mocked(api.listPortfoliosApiV1PortfoliosGet).mockRejectedValue(new Error('offline'));
    await act(async () => { await queryClient.refetchQueries({ queryKey: queryKeys.portfolios.all }); });
    expect(await screen.findByText(/组合更新失败/)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument(); expect(name).toHaveValue('刷新失败也保留');
  });
  it('parent list refresh preserves settings draft and blocks stale save until explicit reload', async () => {
    const user = userEvent.setup(); const { queryClient } = renderWithRouter(<PortfolioList selectedId="p1" onSelect={() => {}} />);
    await user.click(await screen.findByRole('button', { name: '组合设置' }));
    const name = screen.getByLabelText('名称'); await user.clear(name); await user.type(name, '我的草稿');
    act(() => { queryClient.setQueryData(queryKeys.portfolios.all, { pages: [{ items: [{ ...portfolio, version: 2, name: '服务器新名称' }], nextCursor: null }], pageParams: [undefined] }); });
    expect(screen.getByRole('dialog')).toBeInTheDocument(); expect(name).toHaveValue('我的草稿'); await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeDisabled());
    await user.click(screen.getByRole('button', { name: '放弃草稿并加载最新设置' }));
    expect(screen.getByLabelText('名称')).toHaveValue('服务器新名称'); expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
  });
  it('saving an invalid collapsed field expands its group and focuses the input', async () => {
    const user = userEvent.setup(); renderWithRouter(<PortfolioList selectedId="p1" onSelect={() => {}} />);
    await user.click(await screen.findByRole('button', { name: '组合设置' })); await user.click(screen.getByRole('button', { name: /仓位上限/ }));
    const input = screen.getByLabelText('总仓位上限'); await user.clear(input); await user.type(input, '150');
    await user.click(screen.getByRole('button', { name: /仓位上限/ })); await user.click(screen.getByRole('button', { name: '保存' }));
    expect(input).toBeVisible(); expect(input).toHaveFocus();
    expect(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).not.toHaveBeenCalled();
  });
  it('settings uses percentages in UI and preserves untouched raw precision', async () => {
    vi.mocked(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).mockResolvedValue(envelope(portfolio) as never);
    const user = userEvent.setup(); renderWithRouter(<PortfolioList selectedId="p1" onSelect={() => {}} />);
    await user.click(await screen.findByRole('button', { name: '组合设置' })); await user.click(screen.getByRole('button', { name: /仓位上限/ }));
    const input = screen.getByLabelText('总仓位上限'); expect(input).toHaveValue('80'); await user.clear(input); await user.type(input, '75');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).toHaveBeenCalled());
    expect(vi.mocked(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).mock.calls[0][1]).toMatchObject({ expected_version: 1, max_total_position_pct: .75, risk_per_trade_pct: portfolio.risk_per_trade_pct });
  });
  it('requires selected profile budgets before saving admission scope', async () => {
    vi.mocked(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).mockResolvedValue(envelope(portfolio) as never);
    const user = userEvent.setup(); renderWithRouter(<PortfolioList selectedId="p1" onSelect={() => {}} />);
    await user.click(await screen.findByRole('button', { name: '组合设置' }));
    const profile = screen.getByRole('combobox', { name: /策略资格风险档位/ });
    expect(profile).toHaveValue('');
    await user.selectOptions(profile, 'BALANCED');
    await user.click(screen.getByRole('button', { name: '保存' }));
    expect(screen.getByText(/单笔风险比例超过均衡档上限/)).toBeInTheDocument();
    expect(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).not.toHaveBeenCalled();
    for (const [label, value] of [
      ['单笔风险比例', '0.5'], ['组合开放风险上限', '4'],
      ['总仓位上限', '75'], ['单票市值上限', '8'],
      ['最大回撤熔断', '15'], ['行业开放风险上限', '2'],
      ['单日新增风险上限', '2'],
    ]) {
      const input = screen.getByLabelText(label);
      await user.clear(input); await user.type(input, value);
    }
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).toHaveBeenCalled());
    expect(vi.mocked(api.updatePortfolioApiV1PortfoliosPortfolioIdPatch).mock.calls[0][1]).toMatchObject({ risk_profile: 'BALANCED', risk_per_trade_pct: .005 });
  });
  it('position background revision change retains draft and cannot rebase it silently', async () => {
    const user = userEvent.setup(); const { queryClient } = renderWithRouter(<PositionList portfolioId="p1" />);
    await user.type(await screen.findByLabelText('标的代码'), '600000.SH'); await user.type(screen.getByLabelText('数量'), '120'); await user.type(screen.getByLabelText('平均成本'), '10');
    act(() => { queryClient.setQueryData(queryKeys.portfolios.detail('p1'), { revision: 5, items: [] }); });
    expect(screen.getByLabelText('数量')).toHaveValue(120); await waitFor(() => expect(screen.getByRole('button', { name: '添加持仓' })).toBeDisabled());
    expect(api.upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut).not.toHaveBeenCalled();
  });
  it('409 keeps the raw quantity and requires explicit reload', async () => {
    vi.mocked(api.upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut).mockRejectedValue(new ApiError({ code: 'VERSION_CONFLICT', status: 409, retryable: false }));
    const user = userEvent.setup(); renderWithRouter(<PositionList portfolioId="p1" />);
    await user.type(await screen.findByLabelText('标的代码'), '600000.SH'); await user.type(screen.getByLabelText('数量'), '120'); await user.type(screen.getByLabelText('平均成本'), '10');
    await user.click(screen.getByRole('button', { name: '添加持仓' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '添加持仓' })).toBeDisabled());
    expect(screen.getByLabelText('数量')).toHaveValue(120);
    expect(vi.mocked(api.upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut).mock.calls[0][3]).toMatchObject({ quantity: 120, expected_portfolio_revision: 4 });
  });
});
