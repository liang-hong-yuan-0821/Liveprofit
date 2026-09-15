import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  drawingsStorageKey,
  hitTestRect,
  hitTestSegment,
  loadDrawings,
  newId,
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
    ];
    storeRaw('000001.SH', JSON.stringify(payload));
    expect(loadDrawings('000001.SH')).toEqual([validHline, validTrend, validRay, validText]);
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
