// test-catalog-begin
// {
//   "purpose": "分析界面 / real ECharts graph geometry：keeps requested spacing ${JSON.stringify(coordinates)}；long labels generate at most two SVG text lines, plus one layer label",
//   "keywords": [
//     "分析界面",
//     "分析任务",
//     "topology_geometry"
//   ],
//   "covers": [
//     "frontend/src/modules/analysis/components/topologyChartOption.ts"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

// @vitest-environment node
import { describe, expect, it } from 'vitest';
import * as echarts from 'echarts';
import { alignTopologyLayers, buildTopologyChartOption, topologyBounds } from '../../../../../frontend/src/modules/analysis/components/topologyChartOption';
describe('real ECharts graph geometry', () => {
  for (const coordinates of [[[0,0],[0,1],[0,11]], [[0,0],[1,0]], [[0,0]], [[0,0],[1,1],[0,11]]]) {
    it(`keeps requested spacing ${JSON.stringify(coordinates)}`, () => {
      const nodes = coordinates.map(([row, order], index) => ({ id: `market:${index}`, label: `Node ${index}`, row, order, color: '#14b8a6' }));
      const bounds = topologyBounds(nodes); const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: bounds.width, height: bounds.height });
      try {
        chart.setOption(buildTopologyChartOption(nodes, [], id => id)); alignTopologyLayers(chart, nodes);
        const cs = (chart as unknown as { getModel(): { getSeriesByIndex(index: number): { coordinateSystem: { dataToPoint(point: number[]): number[] } } } }).getModel().getSeriesByIndex(0).coordinateSystem;
        const points = nodes.map(n => cs.dataToPoint([90 + n.order * 180, 60 + n.row * 120]));
        expect(points[0][1]).toBeCloseTo(60);
        for (let i=1; i<nodes.length; i++) { expect(points[i][0] - points[0][0]).toBeCloseTo((nodes[i].order - nodes[0].order) * 180); expect(points[i][1] - points[0][1]).toBeCloseTo((nodes[i].row - nodes[0].row) * 120); }
      } finally { chart.dispose(); }
    });
  }
  it('long labels generate at most two SVG text lines, plus one layer label', () => {
    const nodes = [{ id: 'market:long', label: 'An exceptionally long investment research analyst label with many words '.repeat(5), row: 0, order: 0, color: '#14b8a6' }];
    const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: 360, height: 180 });
    try { chart.setOption(buildTopologyChartOption(nodes, [], id => id)); const svg = chart.renderToSVGString(); expect((svg.match(/<text\b/g) ?? []).length).toBeLessThanOrEqual(3); expect((svg.match(/<text\b/g) ?? []).length).toBeGreaterThanOrEqual(2); expect(svg).not.toContain(nodes[0].label); } finally { chart.dispose(); }
  });
});
