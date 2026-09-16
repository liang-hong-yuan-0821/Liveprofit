import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsType } from 'echarts';
import type { ConceptTreeNodeDTO } from '../../../api/generated';
import {
  buildConceptTreeOption, nodeClickOf, splitUpDown, type TreemapNodeClick,
} from './conceptTreeOption';

// 概念两层 treemap（板块区块）：涨/跌分两张图，等宽等高上下排列（用户拍板
// 2026-09-14）——上图 = 上涨概念（红），下图 = 下跌概念（绿、停牌灰收尾）。
// 矩形大小=|当日涨跌幅|、颜色=红涨绿跌静态逐节点着色；点击节点经 onEvents.click
// 上报（echarts-for-react 3.0.6 的属性名，仓库先例 AgentTopologyPage/GraphTopologyPanel）。
// 单侧无数据时只渲染另一张（占满全高）。
//
// 缩放保持（用户反馈 2026-09-16）：父组件点击节点会 setState（弹窗开关），
// 重渲染若重建 option（splitUpDown/buildConceptTreeOption 产新对象、内含新闭包）
// 会被 echarts-for-react 判不等 → notMerge 全量 setOption → treemap 内部缩放状态
// 重置回根节点。修复 = 拆分布局与两张图的 option 按 data 引用 useMemo 缓存：
// data 未变（弹窗开关）→ option 同引用 → isEqual 短路跳过 setOption → 缩放保留；
// 榜单日期变化 → data 变 → 重建（新数据缩放重置属预期）。

export interface ConceptTreemapProps {
  data: ConceptTreeNodeDTO[];
  height?: number;
  onNodeClick: (node: TreemapNodeClick) => void;
}

const CHART_GAP = 8;

export function ConceptTreemap({ data, height = 560, onNodeClick }: ConceptTreemapProps) {
  const { up, down } = useMemo(() => splitUpDown(data), [data]);
  const both = up.length > 0 && down.length > 0;
  const chartHeight = both ? Math.floor((height - CHART_GAP) / 2) : height;
  const upOption = useMemo(() => (up.length > 0 ? buildConceptTreeOption(up) : null), [up]);
  const downOption = useMemo(() => (down.length > 0 ? buildConceptTreeOption(down) : null), [down]);
  const onEvents = {
    click: (params: unknown, _instance: EChartsType) => {
      const node = nodeClickOf(params as { data?: unknown });
      if (node) onNodeClick(node);
    },
  };
  const renderChart = (
    label: string,
    option: ReturnType<typeof buildConceptTreeOption> | null,
  ) =>
    option && (
      <section aria-label={`${label}概念`}>
        <ReactECharts
          option={option}
          style={{ height: chartHeight, width: '100%' }}
          notMerge
          onEvents={onEvents}
        />
      </section>
    );

  return (
    <div data-testid="concept-treemap" className="flex flex-col" style={{ gap: CHART_GAP }}>
      {renderChart('上涨', upOption)}
      {renderChart('下跌', downOption)}
    </div>
  );
}
