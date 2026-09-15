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

export interface ConceptTreemapProps {
  data: ConceptTreeNodeDTO[];
  height?: number;
  onNodeClick: (node: TreemapNodeClick) => void;
}

const CHART_GAP = 8;

export function ConceptTreemap({ data, height = 560, onNodeClick }: ConceptTreemapProps) {
  const { up, down } = splitUpDown(data);
  const both = up.length > 0 && down.length > 0;
  const chartHeight = both ? Math.floor((height - CHART_GAP) / 2) : height;
  const onEvents = {
    click: (params: unknown, _instance: EChartsType) => {
      const node = nodeClickOf(params as { data?: unknown });
      if (node) onNodeClick(node);
    },
  };
  const renderChart = (items: ConceptTreeNodeDTO[], label: string) =>
    items.length > 0 && (
      <section aria-label={`${label}概念`}>
        <ReactECharts
          option={buildConceptTreeOption(items)}
          style={{ height: chartHeight, width: '100%' }}
          notMerge
          onEvents={onEvents}
        />
      </section>
    );

  return (
    <div data-testid="concept-treemap" className="flex flex-col" style={{ gap: CHART_GAP }}>
      {renderChart(up, '上涨')}
      {renderChart(down, '下跌')}
    </div>
  );
}
