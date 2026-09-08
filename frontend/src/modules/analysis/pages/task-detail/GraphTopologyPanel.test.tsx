import { act, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReactECharts from 'echarts-for-react';
import type { ExecutionLogsDTO, ExecutionNodeDTO, GraphTopologyDTO } from '../../../../api/generated';
// TopologyNodeDTO/TopologyEdgeDTO/ExecutionFileDTO 携带枚举命名空间（值导入），不能 import type
import { ExecutionFileDTO, TopologyEdgeDTO, TopologyNodeDTO } from '../../../../api/generated';
import { createTestQueryClient, withQueryClient } from '../../../../test/utils';
import { GraphTopologyPanel, STATUS_META } from './GraphTopologyPanel';

vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: {
    getGraphTopologyApiV1AnalysisTasksTaskIdGraphTopologyGet: vi.fn(),
    getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet: vi.fn(),
  },
}));

// jsdom 无 canvas：mock echarts-for-react 捕获 option + onEvents（先例 CandlestickChart.test.tsx）；
// 点击事件经捕获的 onEvents.click 注入触发（真 echarts 点击不可测）
vi.mock('echarts-for-react', () => ({
  default: vi.fn(() => <div data-testid="echarts" />),
}));

import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';

const getTopology = vi.mocked(AnalysisTasksService.getGraphTopologyApiV1AnalysisTasksTaskIdGraphTopologyGet);
const getLogs = vi.mocked(AnalysisTasksService.getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet);
const mockECharts = vi.mocked(ReactECharts);

function makeNode(over: Partial<TopologyNodeDTO> = {}): TopologyNodeDTO {
  return {
    id: 'stock:Bull Researcher',
    label: 'Bull Researcher',
    layer: 'stock',
    row: 2,
    order: 4,
    status: TopologyNodeDTO.status.NOT_EXECUTED,
    invocation_count: 0,
    dirs: [],
    ...over,
  };
}

function makeTopology(over: Partial<GraphTopologyDTO> = {}): GraphTopologyDTO {
  return {
    task_id: 't-1',
    attempt_no: 1,
    available: true,
    generated_at: '2026-09-08T10:00:00Z',
    nodes: [],
    edges: [],
    ...over,
  };
}

function makeLogs(over: Partial<ExecutionLogsDTO> = {}): ExecutionLogsDTO {
  return {
    task_id: 't-1',
    attempt_no: 1,
    available: true,
    generated_at: '2026-09-08T10:00:00Z',
    layers: [],
    ...over,
  };
}

function makeExecNode(over: Partial<ExecutionNodeDTO> = {}): ExecutionNodeDTO {
  return {
    dir: 'stock/005_Bull_Researcher',
    seq: 5,
    node: 'Bull Researcher',
    model: 'gpt-4o',
    meta: { model: 'gpt-4o' },
    llm_req: null,
    llm_res: null,
    tools: [],
    dp_calls: [],
    ...over,
  };
}

function mockTopology(data: GraphTopologyDTO) {
  getTopology.mockResolvedValue({ data, meta: { request_id: 'r', schema_version: 'v1' } } as never);
}

function mockLogs(data: ExecutionLogsDTO) {
  getLogs.mockResolvedValue({ data, meta: { request_id: 'r', schema_version: 'v1' } } as never);
}

function lastOption(): any {
  return mockECharts.mock.calls.at(-1)![0].option;
}

function lastOnEvents(): any {
  return mockECharts.mock.calls.at(-1)![0].onEvents;
}

function clickNode(id: string) {
  lastOnEvents().click({ dataType: 'node', data: { id } });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('GraphTopologyPanel', () => {
  it('available=false 显示空态', async () => {
    mockTopology(makeTopology({ available: false }));
    render(<GraphTopologyPanel taskId="t-1" terminal={false} taskFailed={false} />, {
      wrapper: withQueryClient(createTestQueryClient()),
    });
    expect(await screen.findByText('暂无执行拓扑')).toBeInTheDocument();
    expect(screen.queryByTestId('topology-chart')).not.toBeInTheDocument();
  });

  it('渲染节点/边：状态着色、条件边虚线、running 标记、多实例 ×N', async () => {
    mockTopology(
      makeTopology({
        nodes: [
          makeNode({ id: 's:Screening', label: 'Screening', layer: 'screening', row: 2, order: 0, status: TopologyNodeDTO.status.EXECUTED }),
          makeNode({ id: 'stock:Bull Researcher', status: TopologyNodeDTO.status.RUNNING, invocation_count: 2, dirs: ['a', 'b'] }),
          makeNode({ id: 'stock:Bear Researcher', status: TopologyNodeDTO.status.ERROR }),
          makeNode({ id: 'stock:Risky Analyst', status: TopologyNodeDTO.status.NOT_EXECUTED }),
        ],
        edges: [
          { source: 's:Screening', target: 'stock:Bull Researcher', kind: TopologyEdgeDTO.kind.LOOP, parallel: false },
          { source: 'stock:Bull Researcher', target: 'stock:Bear Researcher', kind: TopologyEdgeDTO.kind.CONDITIONAL, parallel: true },
          { source: 'stock:Bear Researcher', target: 'stock:Risky Analyst', kind: TopologyEdgeDTO.kind.DIRECT, parallel: false },
        ],
      }),
    );
    render(<GraphTopologyPanel taskId="t-1" terminal={false} taskFailed={false} />, {
      wrapper: withQueryClient(createTestQueryClient()),
    });
    await screen.findByTestId('topology-chart');
    const option = lastOption();
    // series 级 tooltip 会整体覆盖全局 tooltip（echarts 级联模型）——必须缺省（Code Review 修复锁定）
    expect(option.series[0].tooltip).toBeUndefined();
    const nodes = option.series[0].data;
    const byId = Object.fromEntries(nodes.map((n: any) => [n.id, n]));
    expect(byId['s:Screening'].itemStyle.color).toBe(STATUS_META.executed.color);
    expect(byId['stock:Bull Researcher'].itemStyle.color).toBe(STATUS_META.running.color);
    expect(byId['stock:Bear Researcher'].itemStyle.color).toBe(STATUS_META.error.color);
    expect(byId['stock:Risky Analyst'].itemStyle.color).toBe(STATUS_META.not_executed.color);
    // running 标记 + 多实例 ×N 进 label
    expect(byId['stock:Bull Researcher'].label.formatter).toContain('×2');
    expect(byId['stock:Bull Researcher'].label.formatter).toContain('执行中');
    const links = option.series[0].links;
    expect(links[0].lineStyle.type).toBe('dashed'); // loop
    expect(links[0].label.formatter).toBe('逐票循环');
    expect(links[1].lineStyle.type).toBe('dashed'); // conditional
    expect(links[1].lineStyle.curveness).toBe(0.25); // parallel 弧线
    expect(links[2].lineStyle.type).toBe('solid');
    // 图例四状态
    for (const meta of Object.values(STATUS_META)) {
      expect(screen.getByText(meta.label)).toBeInTheDocument();
    }
  });

  it('点击节点打开弹窗并展示实例日志（复用 execution-logs 缓存）', async () => {
    mockTopology(
      makeTopology({
        nodes: [
          makeNode({
            id: 'stock:Bull Researcher',
            status: TopologyNodeDTO.status.EXECUTED,
            invocation_count: 1,
            dirs: ['stock/005_Bull_Researcher'],
          }),
        ],
      }),
    );
    mockLogs(
      makeLogs({
        layers: [
          {
            name: 'stock',
            nodes: [
              makeExecNode({
                llm_res: {
                  path: 'stock/005_Bull_Researcher/res.md',
                  kind: ExecutionFileDTO.kind.MD,
                  content: '看多结论',
                  parse_error: false,
                  truncated: false,
                  total_bytes: 12,
                },
              }),
            ],
          },
        ],
      }),
    );
    render(<GraphTopologyPanel taskId="t-1" terminal={false} taskFailed={false} />, {
      wrapper: withQueryClient(createTestQueryClient()),
    });
    await screen.findByTestId('topology-chart');
    clickNode('stock:Bull Researcher');
    expect(await screen.findByTestId('node-logs-dialog')).toBeInTheDocument();
    expect(screen.getByText('Bull Researcher')).toBeInTheDocument();
    // 弹窗开启时才订阅执行日志（同缓存，异步返回后渲染实例内容）
    expect(await screen.findByText('看多结论')).toBeInTheDocument();
  });

  it('点击未执行节点 → 弹窗空态', async () => {
    mockTopology(
      makeTopology({ nodes: [makeNode({ id: 'stock:Bull Researcher', status: TopologyNodeDTO.status.NOT_EXECUTED })] }),
    );
    render(<GraphTopologyPanel taskId="t-1" terminal={false} taskFailed={false} />, {
      wrapper: withQueryClient(createTestQueryClient()),
    });
    await screen.findByTestId('topology-chart');
    clickNode('stock:Bull Researcher');
    expect(await screen.findByText('该节点本次未执行')).toBeInTheDocument();
  });

  it('弹窗内容随轮询数据实时派生（不滞留点击时刻快照）', async () => {
    mockTopology(
      makeTopology({
        nodes: [
          makeNode({
            id: 'stock:Bull Researcher',
            status: TopologyNodeDTO.status.RUNNING,
            invocation_count: 1,
            dirs: ['stock/005_Bull_Researcher'],
          }),
        ],
      }),
    );
    const queryClient = createTestQueryClient();
    render(<GraphTopologyPanel taskId="t-1" terminal={false} taskFailed={false} />, {
      wrapper: withQueryClient(queryClient),
    });
    await screen.findByTestId('topology-chart');
    clickNode('stock:Bull Researcher');
    const dialog = await screen.findByTestId('node-logs-dialog');
    // 图例也含「执行中」文案，断言限定在弹窗内
    expect(within(dialog).getByText('执行中')).toBeInTheDocument();

    // 数据刷新：节点转 error、多实例 ×2 → 弹窗徽标与计数跟随最新 data；
    // dirs 重派生直接锁定：新增实例 007 在弹窗内出现实例行
    mockTopology(
      makeTopology({
        nodes: [
          makeNode({
            id: 'stock:Bull Researcher',
            status: TopologyNodeDTO.status.ERROR,
            invocation_count: 2,
            dirs: ['stock/005_Bull_Researcher', 'stock/007_Bull_Researcher'],
          }),
        ],
      }),
    );
    mockLogs(
      makeLogs({
        layers: [
          {
            name: 'stock',
            nodes: [makeExecNode({}), makeExecNode({ dir: 'stock/007_Bull_Researcher', seq: 7 })],
          },
        ],
      }),
    );
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['analysis-task', 'graph-topology', 't-1'] });
    });
    await waitFor(() => expect(within(dialog).getByText('出错')).toBeInTheDocument());
    expect(within(dialog).getByText('×2')).toBeInTheDocument();
    expect(await within(dialog).findByText(/stock\/007_Bull_Researcher/)).toBeInTheDocument();
  });

  it('taskFailed 且无 error 节点 → 任务级失败提示；有 error 节点 → 不显示', async () => {
    mockTopology(makeTopology({ nodes: [makeNode({ status: TopologyNodeDTO.status.EXECUTED })] }));
    const queryClient = createTestQueryClient();
    const { rerender } = render(
      <GraphTopologyPanel taskId="t-1" terminal={true} taskFailed={true} />,
      { wrapper: withQueryClient(queryClient) },
    );
    expect(await screen.findByText(/任务失败，但未定位到节点级错误/)).toBeInTheDocument();

    mockTopology(makeTopology({ nodes: [makeNode({ status: TopologyNodeDTO.status.ERROR })] }));
    rerender(<GraphTopologyPanel taskId="t-1" terminal={true} taskFailed={true} />);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['analysis-task', 'graph-topology', 't-1'] });
    });
    await waitFor(() =>
      expect(screen.queryByText(/任务失败，但未定位到节点级错误/)).not.toBeInTheDocument(),
    );
  });

  it('非终态 refetchInterval=5000；终态翻转为 false（停轮询）', async () => {
    mockTopology(makeTopology({ available: false }));
    const queryClient = createTestQueryClient();
    const { rerender } = render(<GraphTopologyPanel taskId="t-1" terminal={false} taskFailed={false} />, {
      wrapper: withQueryClient(queryClient),
    });
    await screen.findByText('暂无执行拓扑');

    const find = () =>
      queryClient
        .getQueryCache()
        .find({ queryKey: ['analysis-task', 'graph-topology', 't-1'] })
        ?.observers[0]?.options.refetchInterval;
    expect(find()).toBe(5000);

    rerender(<GraphTopologyPanel taskId="t-1" terminal={true} taskFailed={false} />);
    expect(find()).toBe(false);
  });
});
