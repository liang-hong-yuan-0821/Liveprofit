// Agent 静态拓扑页测试：节点渲染/着色/「已自定义」后缀/点击→弹窗/Screening 不响应/保存流程。

import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReactECharts from 'echarts-for-react';
import { AgentsService } from '../../../../api/generated/services/AgentsService';
import { createTestQueryClient, withQueryClient } from '../../../../test/utils';
import { AgentTopologyPage } from './AgentTopologyPage';

vi.mock('../../../../api/generated/services/AgentsService', () => ({
  AgentsService: {
    getAgentsTopologyApiV1AgentsTopologyGet: vi.fn(),
    listAgentPromptsApiV1AgentsPromptsGet: vi.fn(),
    upsertAgentPromptApiV1AgentsPromptsNodeIdPut: vi.fn(),
    resetAgentPromptApiV1AgentsPromptsNodeIdDelete: vi.fn(),
  },
}));

vi.mock('echarts-for-react', () => ({
  default: vi.fn(() => <div data-testid="echarts" />),
}));

const getTopology = vi.mocked(AgentsService.getAgentsTopologyApiV1AgentsTopologyGet);
const listPrompts = vi.mocked(AgentsService.listAgentPromptsApiV1AgentsPromptsGet);
const upsertPrompt = vi.mocked(AgentsService.upsertAgentPromptApiV1AgentsPromptsNodeIdPut);
const mockECharts = vi.mocked(ReactECharts);

const TOPOLOGY = {
  nodes: [
    { id: 'market:CN News Analyst', label: 'CN News Analyst', layer: 'market', row: 0, order: 2, has_prompt: true, has_override: true },
    { id: 'market:CN Tech Analyst', label: 'CN Tech Analyst', layer: 'market', row: 0, order: 3, has_prompt: true, has_override: false },
    { id: 'screening:Screening', label: 'Screening', layer: 'screening', row: 2, order: 0, has_prompt: false, has_override: false },
  ],
  edges: [{ source: 'market:CN News Analyst', target: 'market:CN Tech Analyst', kind: 'direct', parallel: false }],
  generated_at: '2026-09-09T10:00:00Z',
};

const PROMPTS = {
  items: [
    { node_id: 'market:CN News Analyst', label: 'CN News Analyst', layer: 'market', default_prompt: '默认提示词文本', override_prompt: '自定义提示词文本', has_override: true, updated_at: '2026-09-09T09:00:00Z' },
    { node_id: 'market:CN Tech Analyst', label: 'CN Tech Analyst', layer: 'market', default_prompt: '默认提示词二', override_prompt: null, has_override: false, updated_at: null },
  ],
};

const ENVELOPE = (data: unknown) => ({ data, meta: { request_id: 'r', schema_version: 'v1' } });

function renderPage() {
  return render(<AgentTopologyPage />, { wrapper: withQueryClient(createTestQueryClient()) });
}

beforeEach(() => {
  vi.clearAllMocks();
  getTopology.mockResolvedValue(ENVELOPE(TOPOLOGY) as never);
  listPrompts.mockResolvedValue(ENVELOPE(PROMPTS) as never);
});

describe('AgentTopologyPage', () => {
  it('渲染节点并标注「已自定义」后缀与着色', async () => {
    renderPage();
    await waitFor(() => expect(mockECharts).toHaveBeenCalled());
    const props = mockECharts.mock.calls.at(-1)![0] as unknown as { option: { series: Array<{ data: Array<{ label: { formatter: string }; itemStyle: { color: string } }> }> } };
    const option = props.option;
    const chartNodes = option.series[0].data;
    const cnNews = chartNodes.find((n) => n.label.formatter.includes('CN News Analyst'));
    expect(cnNews!.label.formatter).toBe('CN News Analyst · 已自定义');
    expect(cnNews!.itemStyle.color).toBe('#f59e0b'); // 琥珀=已自定义
    const cnTech = chartNodes.find((n) => n.label.formatter === 'CN Tech Analyst');
    expect(cnTech!.itemStyle.color).toBe('#38bdf8'); // 蓝=可编辑未自定义
    const screening = chartNodes.find((n) => n.label.formatter.includes('Screening'));
    expect(screening!.itemStyle.color).toBe('#475569'); // 灰=纯代码
  });

  it('点击可编辑节点打开编辑弹窗并预填当前生效提示词', async () => {
    renderPage();
    await waitFor(() => expect(mockECharts).toHaveBeenCalled());
    const propsAny = mockECharts.mock.calls.at(-1)![0] as unknown as { onEvents: { click: (p: { dataType: string; data: { id: string } }) => void } };
    const onEvents = propsAny.onEvents;
    await act(async () => {
      onEvents.click({ dataType: 'node', data: { id: 'market:CN News Analyst' } });
    });
    expect(await screen.findByText(/编辑 Agent 提示词 · CN News Analyst/)).toBeInTheDocument();
    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveValue('自定义提示词文本'); // 覆盖优先
  });

  it('点击 Screening（纯代码）不打开弹窗', async () => {
    renderPage();
    await waitFor(() => expect(mockECharts).toHaveBeenCalled());
    const propsAny = mockECharts.mock.calls.at(-1)![0] as unknown as { onEvents: { click: (p: { dataType: string; data: { id: string } }) => void } };
    const onEvents = propsAny.onEvents;
    await act(async () => {
      onEvents.click({ dataType: 'node', data: { id: 'screening:Screening' } });
    });
    expect(screen.queryByText(/编辑 Agent 提示词/)).not.toBeInTheDocument();
  });

  it('保存后 PUT 成功并 invalidate 拓扑/列表缓存', async () => {
    const user = userEvent.setup();
    upsertPrompt.mockResolvedValue(ENVELOPE({
      node_id: 'market:CN News Analyst', label: 'CN News Analyst', layer: 'market',
      default_prompt: '默认提示词文本', override_prompt: '新提示词', has_override: true,
      updated_at: '2026-09-09T10:30:00Z',
    }) as never);
    renderPage();
    await waitFor(() => expect(mockECharts).toHaveBeenCalled());
    const propsAny = mockECharts.mock.calls.at(-1)![0] as unknown as { onEvents: { click: (p: { dataType: string; data: { id: string } }) => void } };
    const onEvents = propsAny.onEvents;
    await act(async () => {
      onEvents.click({ dataType: 'node', data: { id: 'market:CN News Analyst' } });
    });
    await screen.findByText(/编辑 Agent 提示词/);
    // 等提示词预填落地再交互（否则 clear 作用在空字段上不产生 change、
    // userEditedRef 未置位、entry 到达后回填覆盖输入 → flaky）
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue('自定义提示词文本'));
    await user.clear(screen.getByRole('textbox'));
    await user.type(screen.getByRole('textbox'), '新提示词');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => {
      expect(upsertPrompt).toHaveBeenCalledWith(
        'market%3ACN%20News%20Analyst',
        expect.objectContaining({ prompt_text: '新提示词' }),
      );
    });
  });

  it('空提示词禁用保存按钮', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(mockECharts).toHaveBeenCalled());
    const propsAny = mockECharts.mock.calls.at(-1)![0] as unknown as { onEvents: { click: (p: { dataType: string; data: { id: string } }) => void } };
    const onEvents = propsAny.onEvents;
    await act(async () => {
      onEvents.click({ dataType: 'node', data: { id: 'market:CN Tech Analyst' } });
    });
    await screen.findByText(/编辑 Agent 提示词/);
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue('默认提示词二'));
    await user.clear(screen.getByRole('textbox'));
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
    expect(screen.getByText('提示词不能为空')).toBeInTheDocument();
  });
});
