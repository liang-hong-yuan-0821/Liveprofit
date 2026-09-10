// 共享拓扑 option 构造器单测：固定网格坐标、颜色透传、标签后缀、虚线/曲线、行标签、tooltip 分派。

import { describe, expect, it } from 'vitest';
import {
  buildTopologyChartOption,
  TOPOLOGY_LAYER_LABELS,
  TOPOLOGY_X_ORIGIN,
  TOPOLOGY_X_STEP,
  TOPOLOGY_Y_ORIGIN,
  TOPOLOGY_Y_STEP,
} from './topologyChartOption';

const NODES = [
  { id: 'market:CN News Analyst', label: 'CN News Analyst', row: 0, order: 2, color: '#38bdf8', labelExtra: ' · 已自定义' },
  { id: 'sector:Sector News Analyst', label: 'Sector News Analyst', row: 1, order: 0, color: '#f59e0b' },
];

const EDGES = [
  { source: 'market:CN News Analyst', target: 'sector:Sector News Analyst', kind: 'direct' as const, parallel: false },
  { source: 'a', target: 'b', kind: 'conditional' as const, parallel: false },
  { source: 'c', target: 'd', kind: 'loop' as const, parallel: true },
];

describe('buildTopologyChartOption', () => {
  const option = buildTopologyChartOption(NODES, EDGES, (id) => `tip:${id}`);

  it('节点坐标 = 固定网格（x = origin + order*step，y = origin + row*step）', () => {
    const data = option.series[0].data as Array<{ x: number; y: number }>;
    expect(data[0]).toMatchObject({
      x: TOPOLOGY_X_ORIGIN + 2 * TOPOLOGY_X_STEP,
      y: TOPOLOGY_Y_ORIGIN + 0 * TOPOLOGY_Y_STEP,
    });
    expect(data[1]).toMatchObject({
      x: TOPOLOGY_X_ORIGIN + 0 * TOPOLOGY_X_STEP,
      y: TOPOLOGY_Y_ORIGIN + 1 * TOPOLOGY_Y_STEP,
    });
  });

  it('节点颜色与标签后缀透传', () => {
    const data = option.series[0].data as Array<{ itemStyle: { color: string }; label: { formatter: string } }>;
    expect(data[0].itemStyle.color).toBe('#38bdf8');
    expect(data[0].label.formatter).toBe('CN News Analyst · 已自定义');
    expect(data[1].itemStyle.color).toBe('#f59e0b');
  });

  it('边样式：direct 实线、conditional/loop 虚线、parallel 曲线、loop 标注', () => {
    const links = option.series[0].links as Array<{ lineStyle: { type: string; curveness: number }; label?: object }>;
    expect(links[0].lineStyle.type).toBe('solid');
    expect(links[1].lineStyle.type).toBe('dashed');
    expect(links[2].lineStyle.type).toBe('dashed');
    expect(links[2].lineStyle.curveness).toBe(0.25);
    expect(links[0].lineStyle.curveness).toBe(0);
    expect(links[2].label).toBeDefined();
  });

  it('行标签按层名映射（market 市场层 / sector 板块层）', () => {
    const texts = option.graphic.map((g: { style: { text: string } }) => g.style.text);
    expect(texts).toContain(TOPOLOGY_LAYER_LABELS.market);
    expect(texts).toContain(TOPOLOGY_LAYER_LABELS.sector);
  });

  it('tooltip：节点分派给 nodeTooltip、边显示 source → target', () => {
    const formatter = option.tooltip.formatter as (p: { dataType?: string; data?: { id?: string; source?: string; target?: string } }) => string;
    expect(formatter({ dataType: 'node', data: { id: 'x' } })).toBe('tip:x');
    expect(formatter({ dataType: 'edge', data: { source: 's', target: 't' } })).toBe('s → t');
    expect(formatter({ dataType: 'node' })).toBe('');
  });
});
