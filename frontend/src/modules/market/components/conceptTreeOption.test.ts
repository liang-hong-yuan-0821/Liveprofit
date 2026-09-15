import { describe, expect, it } from 'vitest';
import type { ConceptTreeNodeDTO } from '../../../api/generated';
import {
  PCT_DOWN_COLOR, PCT_NULL_COLOR, PCT_UP_COLOR, MIN_TILE_VALUE,
  buildConceptTreeOption, nodeClickOf, pctColor, splitUpDown,
} from './conceptTreeOption';

function makeConcept(overrides: Partial<ConceptTreeNodeDTO> = {}): ConceptTreeNodeDTO {
  return {
    sector_code: 'BK1753',
    sector_name: '光刻胶',
    rank: 1,
    heat_score: 5.77,
    pct_chg: 1.23,
    member_total: 2,
    members: [
      { ts_code: '600050.SH', name: '中国联通', pct_chg: 3.21 },
      { ts_code: '600051.SH', name: '停牌股', pct_chg: null },
      { ts_code: '600052.SH', name: '下跌股', pct_chg: -2.5 },
    ],
    ...overrides,
  };
}

describe('pctColor', () => {
  it('正涨红 / 负跌绿 / null 灰', () => {
    expect(pctColor(1.0)).toBe(PCT_UP_COLOR);
    expect(pctColor(-1.0)).toBe(PCT_DOWN_COLOR);
    expect(pctColor(null)).toBe(PCT_NULL_COLOR);
  });
});

describe('buildConceptTreeOption', () => {
  it('展示排序（用户拍板修订）：完全按涨跌幅降序——热度分不参与（fixture 热度与涨幅反向验证）、停牌灰最后；个股层同规则', () => {
    const option = buildConceptTreeOption([
      makeConcept({ sector_code: 'A', sector_name: '跌多', heat_score: 500, pct_chg: -8.0 }),
      makeConcept({ sector_code: 'B', sector_name: '涨小', heat_score: 900, pct_chg: 1.0 }),
      makeConcept({ sector_code: 'C', sector_name: '停更', heat_score: 900, pct_chg: null }),
      makeConcept({ sector_code: 'D', sector_name: '涨大', heat_score: 100, pct_chg: 5.0 }),
      makeConcept({ sector_code: 'E', sector_name: '跌少', heat_score: 700, pct_chg: -0.3 }),
    ]);
    const data = option.series[0].data as Array<{
      name: string; children: Array<{ name: string }>;
    }>;
    // 涨大(5.0, heat 100) 排在 涨小(1.0, heat 900) 前——热度不参与排序
    expect(data.map((d) => d.name)).toEqual(['涨大', '涨小', '跌少', '跌多', '停更']);
    // 个股层：涨(3.21) → 跌(-2.5，跌少) → 灰(null)
    expect(data[0].children.map((c) => c.name)).toEqual(['中国联通', '下跌股', '停牌股']);
  });
  it('概念层：value=|当日涨跌幅|（面积保底、停牌最小块）——热度分不参与；颜色静态写入节点 itemStyle', () => {
    const option = buildConceptTreeOption([
      makeConcept({ heat_score: 5.77, pct_chg: 1.23 }),
      makeConcept({ sector_code: 'BK1754', sector_name: '半导体', heat_score: -3.0, pct_chg: -1.5 }),
      makeConcept({ sector_code: 'BK1755', sector_name: '芯片', heat_score: 0, pct_chg: null }),
    ]);
    const data = option.series[0].data as Array<{
      name: string; value: number; itemStyle: { color: string };
      children: Array<{ name: string; value: number; itemStyle: { color: string } }>;
    }>;
    expect(data[0].value).toBe(1.23 + MIN_TILE_VALUE);
    expect(data[0].itemStyle.color).toBe(PCT_UP_COLOR);
    expect(data[1].value).toBe(1.5 + MIN_TILE_VALUE);   // |pct| 参与，热度 -3.0 无关
    expect(data[1].itemStyle.color).toBe(PCT_DOWN_COLOR);
    expect(data[2].value).toBe(MIN_TILE_VALUE);         // 停牌灰最小块
    expect(data[2].itemStyle.color).toBe(PCT_NULL_COLOR);
  });

  it('个股层：value=|pct|+兜底、停牌灰块、静态色两层全写', () => {
    const option = buildConceptTreeOption([makeConcept()]);
    // 展示排序：涨(3.21) → 跌(-2.5) → 灰(null)
    const children = option.series[0].data[0].children as Array<{
      value: number; itemStyle: { color: string };
    }>;
    expect(children[0].value).toBe(3.21 + MIN_TILE_VALUE);
    expect(children[0].itemStyle.color).toBe(PCT_UP_COLOR);
    expect(children[1].value).toBe(2.5 + MIN_TILE_VALUE);
    expect(children[1].itemStyle.color).toBe(PCT_DOWN_COLOR);
    expect(children[2].value).toBe(MIN_TILE_VALUE);   // 停牌无行情
    expect(children[2].itemStyle.color).toBe(PCT_NULL_COLOR);
  });

  it('levels 两层边框/间距、无 colorSaturation（与静态色互斥）', () => {
    const option = buildConceptTreeOption([makeConcept()]);
    const levels = option.series[0].levels as Array<{ itemStyle: Record<string, unknown> }>;
    expect(levels).toHaveLength(2);
    expect(levels[0].itemStyle).toEqual({ borderWidth: 0, gapWidth: 5 });
    expect(levels[1].itemStyle).toEqual({ gapWidth: 1, borderColorSaturation: 0.6 });
    expect(JSON.stringify(option)).not.toContain('colorSaturation');
  });

  it('sort 显式 false + 无 visibleMin + 关面包屑：布局保留数据顺序（series 默认 sort:true 会降级为 desc 按 |pct| 重排，破坏正负分组）', () => {
    const option = buildConceptTreeOption([makeConcept()]);
    expect(option.series[0].sort).toBe(false);
    expect('visibleMin' in option.series[0]).toBe(false);
    expect(option.series[0].breadcrumb).toEqual({ show: false });
  });

  it('概念层名称走 upperLabel（系列级，非叶子节点 label 仅 upperLabel 分支生效，实测）', () => {
    const option = buildConceptTreeOption([makeConcept()]);
    const upper = option.series[0].upperLabel as {
      show: boolean; height: number;
      formatter: (params: { data?: { name?: string; pct_chg?: number | null } }) => string;
    };
    expect(upper.show).toBe(true);
    expect(upper.height).toBe(18);
    // 概念名称条同样带当日涨跌幅
    expect(upper.formatter({ data: { name: '光刻胶', pct_chg: 1.23 } })).toBe('光刻胶 +1.23%');
    expect(upper.formatter({ data: { name: '停更板块', pct_chg: null } })).toBe('停更板块 —');
  });

  it('个股 label：名称旁带当日涨跌幅（停牌显示 —）；个股节点数据携带 pct_chg', () => {
    const option = buildConceptTreeOption([makeConcept()]);
    const children = option.series[0].data[0].children as Array<{ pct_chg: number | null }>;
    expect(children[0].pct_chg).toBe(3.21);
    expect(children[2].pct_chg).toBeNull();   // 排序后停牌灰块在最后
    const formatter = option.series[0].label.formatter as (params: {
      data?: { ts_code?: string; name?: string; pct_chg?: number | null };
    }) => string;
    expect(formatter({ data: { ts_code: '600050.SH', name: '中国联通', pct_chg: 3.21 } }))
      .toBe('中国联通 +3.21%');
    expect(formatter({ data: { ts_code: '600051.SH', name: '停牌股', pct_chg: null } }))
      .toBe('停牌股 —');
    expect(formatter({ data: { name: '光刻胶' } })).toBe('光刻胶');
    expect(formatter({ data: undefined })).toBe('');
  });

  it('tooltip：treePathInfo 拼路径、概念层显示热度分与当日涨跌幅、个股层显示当日涨跌幅', () => {
    const option = buildConceptTreeOption([makeConcept()]);
    const formatter = option.tooltip.formatter as (params: {
      data?: Record<string, unknown>; treePathInfo?: { name: string }[];
    }) => string;
    // treePathInfo 首元素为虚拟根（实测空串）——断言无前导 " › " 残影
    const conceptTip = formatter({
      data: { sector_code: 'BK1753' },
      treePathInfo: [{ name: '' }, { name: '光刻胶' }],
    });
    expect(conceptTip).toContain('光刻胶');
    expect(conceptTip).not.toContain('›');            // 概念层单层：无路径行（与标题重名）
    expect(conceptTip).not.toContain('热度分');        // 热度算法已删（用户拍板修订）
    expect(conceptTip).toContain('+1.23%');
    const memberTip = formatter({
      data: { ts_code: '600050.SH' },
      treePathInfo: [{ name: '' }, { name: '光刻胶' }, { name: '中国联通' }],
    });
    expect(memberTip).toContain('光刻胶 › 中国联通');  // treePathInfo 路径前缀（所属概念上下文）
    expect(memberTip).not.toContain('› 光刻胶 › 中国联通');  // 无虚拟根残影
    expect(memberTip).toContain('+3.21%');
    const nullMemberTip = formatter({ data: { ts_code: '600051.SH' } });
    expect(nullMemberTip).toContain('—');            // 停牌无涨跌幅
    expect(formatter({ data: undefined })).toBe('');
  });
});

describe('splitUpDown', () => {
  it('pct>0 → 上图；pct≤0 与 null（停牌灰）→ 下图', () => {
    const items = [
      makeConcept({ sector_code: 'A', pct_chg: 5.0 }),
      makeConcept({ sector_code: 'B', pct_chg: -2.0 }),
      makeConcept({ sector_code: 'C', pct_chg: 0.0 }),
      makeConcept({ sector_code: 'D', pct_chg: null }),
    ];
    const { up, down } = splitUpDown(items);
    expect(up.map((c) => c.sector_code)).toEqual(['A']);
    expect(down.map((c) => c.sector_code)).toEqual(['B', 'C', 'D']);
  });
});

describe('nodeClickOf', () => {
  it('sector_code → 概念 / ts_code → 个股 / 无 data → null', () => {
    expect(nodeClickOf({ data: { sector_code: 'BK1753', name: '光刻胶' } }))
      .toEqual({ kind: 'concept', code: 'BK1753', name: '光刻胶' });
    expect(nodeClickOf({ data: { ts_code: '600050.SH', name: '中国联通' } }))
      .toEqual({ kind: 'stock', code: '600050.SH', name: '中国联通' });
    expect(nodeClickOf({ data: undefined })).toBeNull();
  });
});
