import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../api/client';
import { renderWithRouter } from '../../../test/utils';
import WatchlistPage from './WatchlistPage';

vi.mock('../../../api/generated/services/WatchlistsService', () => ({
  WatchlistsService: {
    listWatchlistsApiV1WatchlistsGet: vi.fn(),
    createWatchlistApiV1WatchlistsPost: vi.fn(),
    renameWatchlistApiV1WatchlistsWatchlistIdPatch: vi.fn(),
    deleteWatchlistApiV1WatchlistsWatchlistIdDelete: vi.fn(),
    listWatchlistItemsApiV1WatchlistsWatchlistIdItemsGet: vi.fn(),
    addWatchlistItemApiV1WatchlistsWatchlistIdItemsPost: vi.fn(),
    reorderWatchlistItemsApiV1WatchlistsWatchlistIdItemsOrderPut: vi.fn(),
    removeWatchlistItemApiV1WatchlistsWatchlistIdItemsItemIdDelete: vi.fn(),
  },
}));
vi.mock('../../../api/generated/services/PortfoliosService', () => ({
  PortfoliosService: {
    listPortfoliosApiV1PortfoliosGet: vi.fn(),
    createPortfolioApiV1PortfoliosPost: vi.fn(),
    renamePortfolioApiV1PortfoliosPortfolioIdPatch: vi.fn(),
    deletePortfolioApiV1PortfoliosPortfolioIdDelete: vi.fn(),
    listPositionsApiV1PortfoliosPortfolioIdPositionsGet: vi.fn(),
    upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut: vi.fn(),
    removePositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolDelete: vi.fn(),
  },
}));

import { WatchlistsService } from '../../../api/generated/services/WatchlistsService';
import { PortfoliosService } from '../../../api/generated/services/PortfoliosService';

const listWatchlistsMock = WatchlistsService.listWatchlistsApiV1WatchlistsGet as Mock;
const createWatchlistMock = WatchlistsService.createWatchlistApiV1WatchlistsPost as Mock;
const deleteWatchlistMock = WatchlistsService.deleteWatchlistApiV1WatchlistsWatchlistIdDelete as Mock;
const listItemsMock = WatchlistsService.listWatchlistItemsApiV1WatchlistsWatchlistIdItemsGet as Mock;
const addItemMock = WatchlistsService.addWatchlistItemApiV1WatchlistsWatchlistIdItemsPost as Mock;
const reorderItemsMock = WatchlistsService.reorderWatchlistItemsApiV1WatchlistsWatchlistIdItemsOrderPut as Mock;
const listPortfoliosMock = PortfoliosService.listPortfoliosApiV1PortfoliosGet as Mock;
const createPortfolioMock = PortfoliosService.createPortfolioApiV1PortfoliosPost as Mock;
const deletePortfolioMock = PortfoliosService.deletePortfolioApiV1PortfoliosPortfolioIdDelete as Mock;
const listPositionsMock = PortfoliosService.listPositionsApiV1PortfoliosPortfolioIdPositionsGet as Mock;
const upsertPositionMock = PortfoliosService.upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

const group = { id: 'w1', name: '科技', version: 5, item_count: 1, created_at: '', updated_at: '' };
const itemA = {
  id: 'i1', watchlist_id: 'w1', market: 'CN', symbol: '000001.SZ',
  display_order: 0, created_at: '', updated_at: '',
};
const itemB = {
  id: 'i2', watchlist_id: 'w1', market: 'CN', symbol: '600000.SH',
  display_order: 1, created_at: '', updated_at: '',
};
const itemsData = { watchlist_id: 'w1', watchlist_revision: 3, items: [itemA] };
const portfolio = { id: 'p1', name: '核心仓', version: 2, position_count: 0, created_at: '', updated_at: '' };
const positionsData = { portfolio_id: 'p1', portfolio_revision: 7, items: [] };

beforeEach(() => {
  for (const mock of [listWatchlistsMock, createWatchlistMock, deleteWatchlistMock, listItemsMock, addItemMock, reorderItemsMock, listPortfoliosMock, createPortfolioMock, deletePortfolioMock, listPositionsMock, upsertPositionMock]) {
    mock.mockReset();
  }
  listWatchlistsMock.mockResolvedValue(envelope({ items: [group] }));
  listItemsMock.mockResolvedValue(envelope(itemsData));
  listPortfoliosMock.mockResolvedValue(envelope({ items: [portfolio] }));
  listPositionsMock.mockResolvedValue(envelope(positionsData));
});

afterEach(() => {
  vi.clearAllMocks();
});

async function renderPage() {
  renderWithRouter(<WatchlistPage />);
  await screen.findByText('科技');
  await screen.findByText('核心仓');
}

async function openItems() {
  const user = userEvent.setup();
  await renderPage();
  await user.click(screen.getByRole('button', { name: /科技/ }));
  await screen.findByText('000001.SZ');
  return user;
}

async function openPositions() {
  const user = userEvent.setup();
  await renderPage();
  await user.click(screen.getByRole('button', { name: /核心仓/ }));
  await screen.findByLabelText('数量');
  return user;
}

describe('WatchlistPage 自选分组与标的', () => {
  it('分组创建成功：列表以服务端数据刷新，新建输入清空', async () => {
    const user = userEvent.setup();
    await renderPage();
    const callsBefore = listWatchlistsMock.mock.calls.length;

    createWatchlistMock.mockResolvedValue(envelope({ ...group, id: 'w2', name: '消费' }));
    await user.type(screen.getByLabelText('新建分组名称'), '消费');
    await user.click(screen.getAllByRole('button', { name: '新建' })[0]);

    await waitFor(() => expect(listWatchlistsMock.mock.calls.length).toBeGreaterThan(callsBefore));
    expect(screen.getByLabelText('新建分组名称')).toHaveValue('');
    expect(createWatchlistMock).toHaveBeenCalledWith({ name: '消费' });
  });

  it('WATCHLIST_NAME_CONFLICT：保留输入并就地提示名称已存在', async () => {
    const user = userEvent.setup();
    createWatchlistMock.mockRejectedValue(
      new ApiError({ code: 'WATCHLIST_NAME_CONFLICT', message: '分组名已存在', retryable: false, status: 409 }),
    );
    await renderPage();

    await user.type(screen.getByLabelText('新建分组名称'), '科技');
    await user.click(screen.getAllByRole('button', { name: '新建' })[0]);

    expect(await screen.findByText('分组名称已存在')).toBeInTheDocument();
    expect(screen.getByLabelText('新建分组名称')).toHaveValue('科技');
  });

  it('WATCHLIST_NOT_EMPTY：阻止删除非空分组并提示先处理标的', async () => {
    const user = userEvent.setup();
    deleteWatchlistMock.mockRejectedValue(
      new ApiError({ code: 'WATCHLIST_NOT_EMPTY', message: '分组非空', retryable: false, status: 409 }),
    );
    await renderPage();

    // 打开分组删除确认弹窗（第 1 个删除按钮 = 分组行删除）
    await user.click(screen.getAllByRole('button', { name: '删除' })[0]);
    // 弹窗内确认（最后一个"删除"= destructive 确认按钮）
    const confirmButtons = screen.getAllByRole('button', { name: '删除' });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    expect(await screen.findByText('分组非空，请先处理标的后再删除')).toBeInTheDocument();
    expect(deleteWatchlistMock).toHaveBeenCalledWith('w1', 5);
  });

  it('WATCHLIST_ITEM_DUPLICATE：不插入本地乐观项，保留输入提示重复', async () => {
    const user = await openItems();
    addItemMock.mockRejectedValue(
      new ApiError({ code: 'WATCHLIST_ITEM_DUPLICATE', message: '重复标的', retryable: false, status: 409 }),
    );

    await user.type(screen.getAllByLabelText('标的代码')[0], '000001.SZ');
    await user.click(screen.getByRole('button', { name: '添加' }));

    expect(await screen.findByText('该标的已在分组中')).toBeInTheDocument();
    // 列表仍只有原有 1 条（无乐观插入）
    expect(screen.getAllByText('000001.SZ')).toHaveLength(1);
  });

  it('排序冲突：重新拉取服务端顺序（items Query 再次请求）', async () => {
    listItemsMock.mockResolvedValue(envelope({ ...itemsData, items: [itemA, itemB] }));
    const user = await openItems();
    await screen.findByText('600000.SH');
    const itemsCallsBefore = listItemsMock.mock.calls.length;

    reorderItemsMock.mockRejectedValue(
      new ApiError({ code: 'WATCHLIST_ITEM_ORDER_CONFLICT', message: '排序冲突', retryable: true, status: 409 }),
    );

    await user.click(screen.getAllByRole('button', { name: '↓' })[0]);

    await waitFor(() => expect(listItemsMock.mock.calls.length).toBeGreaterThan(itemsCallsBefore));
    // 父列表（分组 item_count）也刷新
    expect(listWatchlistsMock.mock.calls.length).toBeGreaterThan(1);
  });

  it('标的 AI 分析入口：跳转 /ai?create=1 打开新建分析面板（全市场调研，不携带 ticker）', async () => {
    await openItems();

    const link = screen.getByRole('link', { name: 'AI 分析' });
    expect(link).toHaveAttribute('href', '/ai?create=1');
  });
});

describe('WatchlistPage 组合与持仓', () => {
  it('数量必须大于 0：前端先行校验阻止提交', async () => {
    const user = await openPositions();

    await user.type(screen.getAllByLabelText('标的代码').at(-1)!, '000001.SZ');
    await user.type(screen.getByLabelText('数量'), '0');
    await user.type(screen.getByLabelText('平均成本'), '10');
    await user.click(screen.getByRole('button', { name: '新增/修改' }));

    expect(await screen.findByText('数量必须大于 0')).toBeInTheDocument();
    expect(upsertPositionMock).not.toHaveBeenCalled();
  });

  it('INVALID_POSITION 服务端错误映射为字段提示，输入保留', async () => {
    upsertPositionMock.mockRejectedValue(
      new ApiError({ code: 'INVALID_POSITION', message: '非法持仓', retryable: false, status: 422 }),
    );
    const user = await openPositions();

    await user.type(screen.getAllByLabelText('标的代码').at(-1)!, '000001.SZ');
    await user.type(screen.getByLabelText('数量'), '100');
    await user.type(screen.getByLabelText('平均成本'), '10');
    await user.click(screen.getByRole('button', { name: '新增/修改' }));

    expect(await screen.findByText('持仓数据非法：请检查数量与平均成本')).toBeInTheDocument();
    expect(screen.getAllByLabelText('标的代码').at(-1)!).toHaveValue('000001.SZ');
  });

  it('PORTFOLIO_NAME_CONFLICT：保留输入就地提示', async () => {
    const user = userEvent.setup();
    createPortfolioMock.mockRejectedValue(
      new ApiError({ code: 'PORTFOLIO_NAME_CONFLICT', message: '组合名已存在', retryable: false, status: 409 }),
    );
    await renderPage();

    await user.type(screen.getByLabelText('新建组合名称'), '核心仓');
    await user.click(screen.getAllByRole('button', { name: '新建' })[1]);

    expect(await screen.findByText('组合名称已存在')).toBeInTheDocument();
    expect(screen.getByLabelText('新建组合名称')).toHaveValue('核心仓');
  });

  it('删除按 200 envelope（data={deleted, resource_id}）处理并刷新列表', async () => {
    const user = userEvent.setup();
    deletePortfolioMock.mockResolvedValue(envelope({ deleted: true, resource_id: 'p1' }));
    await renderPage();
    const callsBefore = listPortfoliosMock.mock.calls.length;

    // 组合行删除按钮（第二个删除按钮）
    await user.click(screen.getAllByRole('button', { name: '删除' })[1]);
    const confirmButtons = screen.getAllByRole('button', { name: '删除' });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(listPortfoliosMock.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it('AI 建议说明：明确首期不自动同步报告建议仓位', async () => {
    await renderPage();

    expect(screen.getByText(/首期不会将分析报告中的建议仓位/)).toBeInTheDocument();
  });
});
