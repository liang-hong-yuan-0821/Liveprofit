import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  ExecutionDpCallDTO,
  ExecutionLogsDTO,
  ExecutionNodeDTO,
} from '../../../../api/generated';
// ExecutionFileDTO 携带 kind 枚举命名空间（值导入），不能 import type
import { ExecutionFileDTO } from '../../../../api/generated';
import { renderWithRouter, withQueryClient, createTestQueryClient } from '../../../../test/utils';
import { ExecutionLogsPanel } from './ExecutionLogsPanel';

vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: {
    getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet: vi.fn(),
    getExecutionLogContentApiV1AnalysisTasksTaskIdExecutionLogsContentGet: vi.fn(),
  },
}));

import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';

const getLogs = vi.mocked(AnalysisTasksService.getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet);
const getContent = vi.mocked(AnalysisTasksService.getExecutionLogContentApiV1AnalysisTasksTaskIdExecutionLogsContentGet);

function makeFile(over: Partial<ExecutionFileDTO> = {}): ExecutionFileDTO {
  return {
    path: 'market/001_A/res.md',
    kind: ExecutionFileDTO.kind.MD,
    content: '结果文本',
    parse_error: false,
    truncated: false,
    total_bytes: 12,
    ...over,
  };
}

function makeDp(over: Partial<ExecutionDpCallDTO> = {}): ExecutionDpCallDTO {
  return {
    dir: 'market/001_A/001_get',
    name: 'get',
    desc: '获取数据',
    seq: 1,
    ts: 't',
    error: false,
    legacy: false,
    req: { symbol: '000001.SZ' },
    res: null,
    tushare: [],
    ...over,
  };
}

function makeNode(over: Partial<ExecutionNodeDTO> = {}): ExecutionNodeDTO {
  return {
    dir: 'market/001_A',
    seq: 1,
    node: 'A Node',
    model: 'gpt-4o',
    meta: { model: 'gpt-4o' },
    llm_req: null,
    llm_res: null,
    tools: [],
    dp_calls: [],
    ...over,
  };
}

function makeLogs(over: Partial<ExecutionLogsDTO> = {}): ExecutionLogsDTO {
  return {
    task_id: 't-1',
    attempt_no: 1,
    available: true,
    generated_at: '2026-09-06T10:00:00Z',
    layers: [],
    ...over,
  };
}

function mockLogs(data: ExecutionLogsDTO) {
  getLogs.mockResolvedValue({ data, meta: { request_id: 'r', schema_version: 'v1' } } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ExecutionLogsPanel', () => {
  it('available=false 显示空态', async () => {
    mockLogs(makeLogs({ available: false }));
    renderWithRouter(<ExecutionLogsPanel taskId="t-1" terminal={false} />);
    expect(await screen.findByText('暂无执行日志')).toBeInTheDocument();
    expect(screen.getByText(/任务尚未开始写入或本次运行未产生日志/)).toBeInTheDocument();
  });

  it('树渲染 + 自动展开最后一个有内容的 node（所属 layer 同步展开）', async () => {
    const node = makeNode({ llm_res: makeFile({ content: '# 输出全文' }) });
    mockLogs(makeLogs({ layers: [{ name: 'market', nodes: [node] }] }));

    renderWithRouter(<ExecutionLogsPanel taskId="t-1" terminal={false} />);

    // 自动展开后无需点击即可见 node 内容与 layer 标题（markdown 渲染后为 h1 文本节点）
    expect(await screen.findByText('输出全文')).toBeInTheDocument();
    expect(screen.getByText(/001_A（A Node · gpt-4o）/)).toBeInTheDocument();
  });

  it('kind=md 内容按 markdown 渲染（GFM 标题与表格）', async () => {
    const node = makeNode({
      llm_res: makeFile({
        content: '## 二级标题\n\n| 排名 | 行业 |\n|------|------|\n| 1 | 石油石化 |',
      }),
    });
    mockLogs(makeLogs({ layers: [{ name: 'market', nodes: [node] }] }));

    renderWithRouter(<ExecutionLogsPanel taskId="t-1" terminal={false} />);

    expect(await screen.findByRole('heading', { name: '二级标题' })).toBeInTheDocument();
    expect(screen.getByText('石油石化')).toBeInTheDocument();
  });

  it('truncated 文件点「查看完整内容」触发 content 端点拉全量', async () => {
    const user = userEvent.setup();
    const dp = makeDp({ res: makeFile({ path: 'market/001_A/001_get/res.md', content: null, truncated: true, total_bytes: 999 }) });
    mockLogs(makeLogs({ layers: [{ name: 'market', nodes: [makeNode({ dp_calls: [dp] })] }] }));
    getContent.mockResolvedValue({
      data: makeFile({ path: 'market/001_A/001_get/res.md', content: '# 完整内容' }),
      meta: {},
    } as never);

    renderWithRouter(<ExecutionLogsPanel taskId="t-1" terminal={false} />);

    // 展开 DP 调用条目后点击查看完整内容
    await user.click(await screen.findByText(/001_get\s+get — 获取数据/));
    expect(screen.getByText(/内容过大（999 字节）/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '查看完整内容' }));

    expect(getContent).toHaveBeenCalledWith('t-1', 'market/001_A/001_get/res.md');
    // 精确字符串匹配（h1 文本节点）；禁用 /完整内容/ 正则——会同屏命中「查看完整内容」按钮文本
    expect(await screen.findByText('完整内容')).toBeInTheDocument();
  });

  it('自动展开目标随最新 node 前移；用户收起的旧节点不再自动展开', async () => {
    const user = userEvent.setup();
    const nodeA = makeNode({ llm_res: makeFile({ path: 'market/001_A/res.md', content: 'A 结果' }) });
    mockLogs(makeLogs({ layers: [{ name: 'market', nodes: [nodeA] }] }));

    const { queryClient } = renderWithRouter(<ExecutionLogsPanel taskId="t-1" terminal={false} />);
    expect(await screen.findByText('A 结果')).toBeInTheDocument();

    // 用户手动收起 node A（其内容隐藏）
    await user.click(screen.getByText(/001_A（A Node · gpt-4o）/));
    expect(screen.queryByText('A 结果')).not.toBeInTheDocument();

    // 新数据：同数据刷新（node A 仍是最后）→ 不因轮询重新展开
    mockLogs(makeLogs({ layers: [{ name: 'market', nodes: [nodeA] }] }));
    void queryClient.invalidateQueries({ queryKey: ['analysis-task', 'execution-logs', 't-1'] });
    await waitFor(() => expect(getLogs).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('A 结果')).not.toBeInTheDocument();

    // 新 node B 出现（最后有内容）→ 自动展开 B，其所属 layer 同步展开
    const nodeB = makeNode({ dir: 'sector/002_B', node: 'B Node', llm_res: makeFile({ path: 'sector/002_B/res.md', content: 'B 结果' }) });
    mockLogs(makeLogs({ layers: [{ name: 'market', nodes: [nodeA] }, { name: 'sector', nodes: [nodeB] }] }));
    void queryClient.invalidateQueries({ queryKey: ['analysis-task', 'execution-logs', 't-1'] });
    expect(await screen.findByText('B 结果')).toBeInTheDocument();
    // A 保持收起（用户手动收起过）
    expect(screen.queryByText('A 结果')).not.toBeInTheDocument();
  });

  it('非终态 refetchInterval=5000；终态翻转为 false（停轮询）', async () => {
    mockLogs(makeLogs({ available: false }));
    const queryClient = createTestQueryClient();
    const { rerender } = render(<ExecutionLogsPanel taskId="t-1" terminal={false} />, {
      wrapper: withQueryClient(queryClient),
    });
    await screen.findByText('暂无执行日志');

    const find = () =>
      queryClient
        .getQueryCache()
        .find({ queryKey: ['analysis-task', 'execution-logs', 't-1'] })
        ?.observers[0]?.options.refetchInterval;
    expect(find()).toBe(5000);

    rerender(<ExecutionLogsPanel taskId="t-1" terminal={true} />);
    expect(find()).toBe(false);
  });

  it('缺生成时间与轮询提示：运行中显示「每 5 秒自动刷新」，终态不显示', async () => {
    mockLogs(makeLogs({ available: false }));
    const { rerender } = render(<ExecutionLogsPanel taskId="t-1" terminal={false} />, {
      wrapper: withQueryClient(createTestQueryClient()),
    });
    expect(await screen.findByText(/每 5 秒自动刷新/)).toBeInTheDocument();

    rerender(<ExecutionLogsPanel taskId="t-1" terminal={true} />);
    await screen.findByText('暂无执行日志');
    expect(screen.queryByText(/每 5 秒自动刷新/)).not.toBeInTheDocument();
  });
});
