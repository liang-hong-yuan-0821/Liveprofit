import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../test/utils';
import { MacroInformationPanel } from './MacroInformationPanel';

vi.mock('../../../api/generated/services/MacroInformationService', () => ({
  MacroInformationService: { listMacroInformationApiV1MacroInformationGet: vi.fn() },
}));

import { MacroInformationService } from '../../../api/generated/services/MacroInformationService';

const macroMock = MacroInformationService.listMacroInformationApiV1MacroInformationGet as Mock;

function envelope(items: unknown[], nextCursor: string | null = null) {
  return { data: { items }, meta: { request_id: 'r', schema_version: 'v1', next_cursor: nextCursor } };
}

function makeItem(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    event_id: 101,
    title: '宏观事件标题',
    occurred_at: '2026-09-03T02:00:00Z',
    market_tags: ['CN'],
    macro_topic: '货币政策',
    summary: '事件摘要',
    source: '来源A',
    related_assets: ['000001.SH'],
    research_status: '已审核',
    ...overrides,
  };
}

beforeEach(() => {
  macroMock.mockReset();
  macroMock.mockResolvedValue(envelope([makeItem('m1')]));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('MacroInformationPanel', () => {
  it('空列表是正常空态，不从其他来源回填', async () => {
    macroMock.mockResolvedValue(envelope([]));
    renderWithRouter(<MacroInformationPanel />);

    expect(await screen.findByText('暂无可展示的事件研究宏观信息')).toBeInTheDocument();
  });

  it('卡片展示已审核投影字段；标题跳转事件研究并携带 event_id', async () => {
    renderWithRouter(<MacroInformationPanel />);

    expect(await screen.findByText('宏观事件标题')).toBeInTheDocument();
    expect(screen.getByText('事件摘要')).toBeInTheDocument();
    expect(screen.getByText('货币政策')).toBeInTheDocument();
    expect(screen.getByText(/关联资产：000001\.SH/)).toBeInTheDocument();
    expect(screen.getByText(/研究状态：已审核/)).toBeInTheDocument();

    const link = screen.getByRole('link', { name: '宏观事件标题' });
    expect(link).toHaveAttribute('href', '/event-study?event_id=101');
  });

  it('无 event_id 时跳转事件研究不携带参数', async () => {
    macroMock.mockResolvedValue(envelope([makeItem('m1', { event_id: null })]));
    renderWithRouter(<MacroInformationPanel />);

    expect(await screen.findByText('宏观事件标题')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '宏观事件标题' })).toHaveAttribute('href', '/event-study');
  });

  it('market/topic 筛选以服务端参数刷新', async () => {
    const user = userEvent.setup();
    renderWithRouter(<MacroInformationPanel />);
    await screen.findByText('宏观事件标题');

    await user.selectOptions(screen.getByLabelText('市场'), 'CN');
    await waitFor(() => expect(macroMock.mock.calls.at(-1)?.[2]).toBe('CN'));

    await user.type(screen.getByLabelText('主题'), '货币政策');
    await waitFor(() => expect(macroMock.mock.calls.at(-1)?.[3]).toBe('货币政策'));
  });
});
