import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConceptTreemap } from './ConceptTreemap';
import type { ConceptTreeNodeDTO } from '../../../api/generated';

// 缩放保持回归（用户反馈 2026-09-16）：echarts-for-react 对 option 做深比较，
// 同引用才跳过 setOption（notMerge 重建会重置 treemap 内部缩放状态）。mock 捕获
// 每次渲染收到的 option 引用——数据未变时父组件重渲染（弹窗开关 setState）必须
// 拿到同引用；数据变时重建。jsdom 无 canvas，真 echarts 初始化会踩坑，先例
// CandlestickChart.test.tsx 同款 mock 策略。

const mockState = vi.hoisted(() => ({
  options: [] as unknown[],
  onEvents: null as null | Record<string, (params: unknown) => void>,
}));

vi.mock('echarts-for-react', () => ({
  default: (props: { option: unknown; onEvents?: Record<string, (p: unknown) => void> }) => {
    mockState.options.push(props.option);
    if (props.onEvents) mockState.onEvents = props.onEvents;
    return null;
  },
}));

function makeConcept(
  code: string,
  name: string,
  pct: number | null,
  members: Array<{ ts_code: string; name: string; pct_chg: number | null }>,
): ConceptTreeNodeDTO {
  return {
    sector_code: code,
    sector_name: name,
    rank: 1,
    heat_score: 5.77,
    pct_chg: pct,
    member_total: members.length,
    members,
  };
}

const upData: ConceptTreeNodeDTO[] = [
  makeConcept('BK1753', '光刻胶', 1.23, [
    { ts_code: '600050.SH', name: '中国联通', pct_chg: 3.21 },
    { ts_code: '600051.SH', name: '停牌股', pct_chg: null },
  ]),
];

describe('ConceptTreemap', () => {
  it('数据未变时 option 引用稳定（弹窗开关重渲染不重建 → 缩放保留）', () => {
    mockState.options = [];
    const onNodeClick = vi.fn();
    const { rerender } = render(<ConceptTreemap data={upData} onNodeClick={onNodeClick} />);
    expect(mockState.options.length).toBe(1); // 单侧（上涨）一张图

    const firstRenderOptions = [...mockState.options];
    // 模拟点击节点后父组件 setState（弹窗开关）：data 引用未变
    rerender(<ConceptTreemap data={upData} onNodeClick={onNodeClick} />);
    expect(mockState.options).toHaveLength(2);
    expect(mockState.options[1]).toBe(firstRenderOptions[0]);
  });

  it('数据变化时 option 重建（榜单日期切换 → 新数据 → 重建属预期）', () => {
    mockState.options = [];
    const { rerender } = render(<ConceptTreemap data={upData} onNodeClick={() => {}} />);
    const firstRenderOptions = [...mockState.options];

    const newData: ConceptTreeNodeDTO[] = [makeConcept('BK1754', '半导体', 2.5, [])];
    rerender(<ConceptTreemap data={newData} onNodeClick={() => {}} />);
    expect(mockState.options[1]).not.toBe(firstRenderOptions[0]);
  });

  it('点击个股上报 onNodeClick（treemap click 事件接线）', () => {
    mockState.options = [];
    const onNodeClick = vi.fn();
    render(<ConceptTreemap data={upData} onNodeClick={onNodeClick} />);

    expect(mockState.onEvents?.click).toBeTypeOf('function');
    mockState.onEvents?.click({ data: { ts_code: '600050.SH', name: '中国联通' } });
    expect(onNodeClick).toHaveBeenCalledWith({ kind: 'stock', code: '600050.SH', name: '中国联通' });
  });

  it('涨跌两侧分别渲染（上涨概念上、下跌概念下）', () => {
    mockState.options = [];
    const both: ConceptTreeNodeDTO[] = [
      ...upData,
      makeConcept('BK1754', '半导体', -2.5, [
        { ts_code: '300001.SZ', name: '成分B', pct_chg: -5.0 },
      ]),
    ];
    render(<ConceptTreemap data={both} onNodeClick={() => {}} />);
    expect(mockState.options).toHaveLength(2);
    expect(screen.getByLabelText('上涨概念')).toBeInTheDocument();
    expect(screen.getByLabelText('下跌概念')).toBeInTheDocument();
  });
});
