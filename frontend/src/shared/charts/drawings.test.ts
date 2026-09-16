import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  drawingsStorageKey,
  hitTestRect,
  hitTestSegment,
  loadDrawings,
  newId,
  renderedSegment,
  saveDrawings,
  type Drawing,
} from './drawings';

const validHline: Drawing = { id: 'a1', kind: 'hline', p1: { date: '2026-09-01', price: 3000 } };
const validTrend: Drawing = {
  id: 'a2', kind: 'trend',
  p1: { date: '2026-09-01', price: 3000 },
  p2: { date: '2026-09-10', price: 3100 },
};
const validRay: Drawing = {
  id: 'a3', kind: 'ray',
  p1: { date: '2026-09-01', price: 3000 },
  p2: { date: '2026-09-10', price: 3100 },
};
const validText: Drawing = { id: 'a4', kind: 'text', pos: { date: '2026-09-05', price: 3050 }, text: '支撑位' };

function storeRaw(symbol: string, value: string) {
  localStorage.setItem(drawingsStorageKey(symbol), value);
}

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('loadDrawings 按 kind 逐项校验（localStorage 是不可信输入）', () => {
  it('非法条目丢弃、合法条目保留', () => {
    const payload = [
      validHline,
      validTrend,
      validRay,
      validText,
      { id: 'b1', kind: 'bogus', p1: { date: '2026-09-01', price: 1 } }, // kind 非法
      { id: 'b2', kind: 'hline', p1: { date: '2026-09-01', price: NaN } }, // NaN
      { id: 'b3', kind: 'hline', p1: { date: '2026/09/01', price: 1 } }, // 日期格式错
      { id: 'b4', kind: 'hline', p1: { date: '2026-09-01', price: 1 }, label: 'x'.repeat(51) }, // label 超长
      { id: 'b5', kind: 'text', text: '无 pos' }, // text 缺 pos
      { id: 'b6', kind: 'text', pos: { date: '2026-09-01', price: 1 }, text: '   ' }, // 空 text
      { id: 'b7', kind: 'trend', p1: { date: '2026-09-01', price: 1 }, p2: { date: '2026-09-01', price: 2 } }, // 两锚点同日
      { id: 'b8', kind: 'trend', p1: { date: '2026-09-01', price: 1 } }, // trend 缺 p2
      { id: 'b9', kind: 'hline' }, // hline 缺 p1
      { kind: 'hline', p1: { date: '2026-09-01', price: 1 } }, // 缺 id
      { id: 'b10', kind: 'hline', p1: { date: '2026-09-01', price: 1, x: NaN } }, // 小数 x 非有限数（JSON 序列化为 null）
    ];
    storeRaw('000001.SH', JSON.stringify(payload));
    expect(loadDrawings('000001.SH')).toEqual([validHline, validTrend, validRay, validText]);
  });

  it('小数锚点 x：有限数保留、缺省（旧数据）通过', () => {
    const withX: Drawing = {
      id: 'x1', kind: 'trend',
      p1: { date: '2026-09-01', price: 3000, x: 0.3 },
      p2: { date: '2026-09-10', price: 3100, x: 9.7 },
    };
    const payload = [withX, validHline]; // validHline 无 x（旧数据形态）
    storeRaw('000001.SH', JSON.stringify(payload));
    expect(loadDrawings('000001.SH')).toEqual(payload);
  });

  it('JSON 解析失败/非数组/超条数上限：安全兜底', () => {
    storeRaw('000001.SH', 'not-json{{');
    expect(loadDrawings('000001.SH')).toEqual([]);
    storeRaw('000001.SH', JSON.stringify({ kind: 'hline' }));
    expect(loadDrawings('000001.SH')).toEqual([]);
    // 条数上限 100：超出的截断
    const many = Array.from({ length: 105 }, (_v, i) => ({
      ...validHline,
      id: `id-${i}`,
      p1: { date: '2026-09-01', price: 3000 + i },
    }));
    storeRaw('000001.SH', JSON.stringify(many));
    expect(loadDrawings('000001.SH')).toHaveLength(100);
  });

  it('无存储时返回 []', () => {
    expect(loadDrawings('000001.SH')).toEqual([]);
  });
});

describe('saveDrawings / loadDrawings 往返', () => {
  it('保存后读回一致、key 按标的隔离', () => {
    const drawings = [validHline, validTrend, validText];
    saveDrawings('000001.SH', drawings);
    expect(localStorage.getItem(drawingsStorageKey('000001.SH'))).toBe(JSON.stringify(drawings));
    expect(loadDrawings('000001.SH')).toEqual(drawings);
    // 切标的不串：另一标的 key 为空
    expect(loadDrawings('399001.SZ')).toEqual([]);
    expect(drawingsStorageKey('000001.SH')).toBe('liveprofit.market.drawings.v1.000001.SH');
  });
});

describe('hitTest 距离判定', () => {
  const p1 = { x: 100, y: 100 };
  const p2 = { x: 200, y: 100 };

  it('端点优先：距端点 ≤8px 命中端点，即使投影距离更近', () => {
    expect(hitTestSegment({ x: 103, y: 104 }, p1, p2)).toBe('endpoint1');
    expect(hitTestSegment({ x: 196, y: 103 }, p1, p2)).toBe('endpoint2');
  });

  it('线身：投影距离 ≤6px 命中 body，超出不命中', () => {
    expect(hitTestSegment({ x: 150, y: 105 }, p1, p2)).toBe('body');
    expect(hitTestSegment({ x: 150, y: 107 }, p1, p2)).toBeNull();
    // 投影落在延长线外：端点距离 >8 时不命中（不把延长线当线身）
    expect(hitTestSegment({ x: 50, y: 100 }, p1, p2)).toBeNull();
    expect(hitTestSegment({ x: 250, y: 100 }, p1, p2)).toBeNull();
  });

  it('退化点（两锚点同像素）：不命中线身', () => {
    expect(hitTestSegment({ x: 100, y: 100 }, p1, p1)).toBe('endpoint1');
    expect(hitTestSegment({ x: 150, y: 100 }, p1, p1)).toBeNull();
  });

  it('矩形命中（text 包围盒估算）：含 6px 外扩', () => {
    const rect = { x1: 100, y1: 100, x2: 160, y2: 120 };
    expect(hitTestRect({ x: 130, y: 110 }, rect)).toBe(true);
    expect(hitTestRect({ x: 103, y: 97 }, rect)).toBe(true); // 外扩 6px 内
    expect(hitTestRect({ x: 93, y: 100 }, rect)).toBe(false); // 距左缘 7px > 6px 外扩
  });
});

describe('newId', () => {
  it('有 randomUUID 时用之；无（非安全上下文）时 fallback 仍返回非空字符串', () => {
    const first = newId();
    expect(typeof first).toBe('string');
    expect(first.length).toBeGreaterThan(0);
    // fallback：stub 掉 crypto（jsdom 默认有 randomUUID）
    vi.stubGlobal('crypto', {});
    const fallback = newId();
    expect(fallback.startsWith('d-')).toBe(true);
    expect(newId()).not.toBe(fallback);
  });
});

describe('renderedSegment（窗口裁切/外推 + extent 求交 + 小数锚点）', () => {
  const drawDates = Array.from({ length: 10 }, (_v, i) => `2026-09-${String(i + 1).padStart(2, '0')}`);
  const window = { startIdx: 4, endIdx: 7 };
  const extent = { min: 2950, max: 3150 };
  const fullWindow = { startIdx: 0, endIdx: 9 };

  it('ray：p1 窗口外左 → 起点收进网格左缘（3.5）、终点外推右缘（7.5）；p1 在窗口右侧跳过', () => {
    const ray: Drawing = {
      id: 'r1', kind: 'ray',
      p1: { date: drawDates[1], price: 3050 },
      p2: { date: drawDates[2], price: 3060 },
    };
    // 斜率 10/类目：linePrice(3.5) = 3050 + 10×2.5 = 3075、linePrice(7.5) = 3115
    expect(renderedSegment(ray, drawDates, window, extent)).toEqual({ i1: 3.5, p1: 3075, i2: 7.5, p2: 3115 });
    const rayRight: Drawing = {
      id: 'r2', kind: 'ray',
      p1: { date: drawDates[8], price: 3000 },
      p2: { date: drawDates[9], price: 3010 },
    };
    expect(renderedSegment(rayRight, drawDates, window, extent)).toBeNull();
  });

  it('trend：两端点窗口同侧跳过；横穿窗口按 extent 求交（交点索引保留小数，回归锚：参数化基准必须是起点）', () => {
    const outside: Drawing = {
      id: 'o1', kind: 'trend',
      p1: { date: drawDates[0], price: 3000 },
      p2: { date: drawDates[1], price: 3010 },
    };
    expect(renderedSegment(outside, drawDates, window, extent)).toBeNull();
    // crossing（5000→1000）：x 截断 [3.5, 7.5] 后两端点仍出 extent、中段横穿 → 求交收进边界。
    // t(3150) = 0.184375 → 3.5 + 0.184375×4 = 4.2375；t(2950) = 0.271875 → 4.5875
    const crossing: Drawing = {
      id: 'c1', kind: 'trend',
      p1: { date: drawDates[1], price: 5000 },
      p2: { date: drawDates[8], price: 1000 },
    };
    const seg = renderedSegment(crossing, drawDates, window, extent);
    expect(seg).not.toBeNull();
    expect(seg!.p1).toBe(3150);
    expect(seg!.p2).toBe(2950);
    expect(seg!.i1).toBeCloseTo(4.2375, 12);
    expect(seg!.i2).toBeCloseTo(4.5875, 12);
  });

  it('hline：两端 = 窗口半开带边缘（网格左右缘同像素）；价格完全越界跳过', () => {
    const hline: Drawing = { id: 'h1', kind: 'hline', p1: { date: drawDates[1], price: 3050 } };
    expect(renderedSegment(hline, drawDates, window, extent)).toEqual({ i1: 3.5, p1: 3050, i2: 7.5, p2: 3050 });
    const far: Drawing = { id: 'h2', kind: 'hline', p1: { date: drawDates[1], price: 100 } };
    expect(renderedSegment(far, drawDates, window, extent)).toBeNull();
  });

  it('小数锚点 x：端点原样保留（起点精确落在按点位置）；越界夹到带内；双端同边界退化回中心防除零', () => {
    const frac: Drawing = {
      id: 'f1', kind: 'trend',
      p1: { date: drawDates[1], price: 3000, x: 1.2 },
      p2: { date: drawDates[3], price: 3100, x: 3.4 },
    };
    expect(renderedSegment(frac, drawDates, fullWindow, { min: 2900, max: 3200 }))
      .toEqual({ i1: 1.2, p1: 3000, i2: 3.4, p2: 3100 });
    // x=9 越出 bar 1 的带 [0.5, 1.5] → 夹到 1.5
    const clamp: Drawing = {
      id: 'f2', kind: 'trend',
      p1: { date: drawDates[1], price: 3000, x: 9 },
      p2: { date: drawDates[3], price: 3100 },
    };
    expect(renderedSegment(clamp, drawDates, fullWindow, { min: 2900, max: 3200 }))
      .toEqual({ i1: 1.5, p1: 3000, i2: 3, p2: 3100 });
    // 相邻 bar 双端都夹到同一边界 1.5 → 退化回整数中心（防除零）
    const degen: Drawing = {
      id: 'f3', kind: 'trend',
      p1: { date: drawDates[1], price: 3000, x: 1.5 },
      p2: { date: drawDates[2], price: 3100, x: 1.5 },
    };
    expect(renderedSegment(degen, drawDates, fullWindow, { min: 2900, max: 3200 }))
      .toEqual({ i1: 1, p1: 3000, i2: 2, p2: 3100 });
    // 窗口左缘 bar 的外侧半带锚点：裁切边界 = 3.5，小数锚点 3.7 原样保留（不吸到 bar 4 中心）
    const edge: Drawing = {
      id: 'f4', kind: 'trend',
      p1: { date: drawDates[4], price: 3000, x: 3.7 },
      p2: { date: drawDates[6], price: 3100 },
    };
    expect(renderedSegment(edge, drawDates, window, extent))
      .toEqual({ i1: 3.7, p1: 3000, i2: 6, p2: 3100 });
  });
});
