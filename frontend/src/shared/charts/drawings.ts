// 画线数据模块（§3.6）：类型 + localStorage 读写 + 校验 + 命中检测纯函数。
// 锚点一律存"日期 + 价格"数据坐标（非像素/索引）：dataZoom 缩放平移与渐进加载
// 前插数据（只前插不移除）时画线天然跟随、不漂移。

export type DrawingAnchor = { date: string; price: number };

export type Drawing =
  | { id: string; kind: 'hline'; p1: DrawingAnchor; label?: string } // 水平线：仅 p1.price 有效
  | { id: string; kind: 'trend'; p1: DrawingAnchor; p2: DrawingAnchor; label?: string }
  | { id: string; kind: 'ray'; p1: DrawingAnchor; p2: DrawingAnchor; label?: string } // 从 p1 过 p2 向右延伸
  | { id: string; kind: 'text'; pos: DrawingAnchor; text: string };

const STORAGE_KEY_PREFIX = 'liveprofit.market.drawings.v1.';
const MAX_DRAWINGS = 100;
const MAX_TEXT_LENGTH = 50;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export function drawingsStorageKey(symbol: string): string {
  return `${STORAGE_KEY_PREFIX}${symbol}`;
}

// id：crypto.randomUUID()（非安全上下文如 http://局域网IP 下该 API 为 undefined——
// jsdom 26 实测有——带 Math.random fallback）
export function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function isValidDate(v: unknown): v is string {
  return typeof v === 'string' && DATE_PATTERN.test(v);
}

function isValidLabel(v: unknown): boolean {
  return v === undefined || (typeof v === 'string' && v.length <= MAX_TEXT_LENGTH);
}

function isValidAnchor(v: unknown): v is DrawingAnchor {
  if (typeof v !== 'object' || v === null) return false;
  const a = v as Record<string, unknown>;
  return isValidDate(a.date) && isFiniteNumber(a.price);
}

// 按 kind 逐项校验（localStorage 是可手改的不可信输入，校验契约必须闭合）：
// 通用项 kind/date/price/label 长度/总条数；按 kind：hline 需 p1；trend/ray 需 p1+p2
// 且日期不同（载入路径不受提交校验覆盖，否则渲染除零出 NaN）；text 需 pos 且 text 非空。
// 任一不过即丢弃该条目。
function isValidDrawing(v: unknown): v is Drawing {
  if (typeof v !== 'object' || v === null) return false;
  const d = v as Record<string, unknown>;
  if (typeof d.id !== 'string' || d.id === '') return false;
  switch (d.kind) {
    case 'hline':
      return isValidAnchor(d.p1) && isValidLabel(d.label);
    case 'trend':
    case 'ray':
      return (
        isValidAnchor(d.p1) &&
        isValidAnchor(d.p2) &&
        (d.p1 as DrawingAnchor).date !== (d.p2 as DrawingAnchor).date &&
        isValidLabel(d.label)
      );
    case 'text':
      return (
        isValidAnchor(d.pos) &&
        typeof d.text === 'string' &&
        d.text.trim() !== '' &&
        d.text.length <= MAX_TEXT_LENGTH
      );
    default:
      return false;
  }
}

export function loadDrawings(symbol: string): Drawing[] {
  try {
    const raw = localStorage.getItem(drawingsStorageKey(symbol));
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, MAX_DRAWINGS).filter(isValidDrawing);
  } catch {
    return []; // JSON 解析失败等一律返回空
  }
}

export function saveDrawings(symbol: string, drawings: Drawing[]): void {
  try {
    localStorage.setItem(drawingsStorageKey(symbol), JSON.stringify(drawings));
  } catch {
    // 存储失败（配额/隐私模式）静默：画线丢失可接受，不影响图表功能
  }
}

// 命中检测（编辑模式，像素空间）：点到线段投影距离 ≤ LINE_HIT_PX 命中线身、
// 距端点 ≤ ENDPOINT_HIT_PX 命中端点（端点优先）；text 按包围盒估算 ≤ LINE_HIT_PX。
export const LINE_HIT_PX = 6;
export const ENDPOINT_HIT_PX = 8;

export function hitTestSegment(
  mouse: { x: number; y: number },
  p1: { x: number; y: number },
  p2: { x: number; y: number },
): 'endpoint1' | 'endpoint2' | 'body' | null {
  const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
    Math.hypot(a.x - b.x, a.y - b.y);
  if (dist(mouse, p1) <= ENDPOINT_HIT_PX) return 'endpoint1';
  if (dist(mouse, p2) <= ENDPOINT_HIT_PX) return 'endpoint2';
  // 点到线段投影距离
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return null; // 退化点（两锚点同日已被提交/载入校验拒绝）
  const t = Math.max(0, Math.min(1, ((mouse.x - p1.x) * dx + (mouse.y - p1.y) * dy) / lenSq));
  const proj = { x: p1.x + t * dx, y: p1.y + t * dy };
  return dist(mouse, proj) <= LINE_HIT_PX ? 'body' : null;
}

export function hitTestRect(
  mouse: { x: number; y: number },
  rect: { x1: number; y1: number; x2: number; y2: number },
): boolean {
  const pad = LINE_HIT_PX;
  return (
    mouse.x >= rect.x1 - pad &&
    mouse.x <= rect.x2 + pad &&
    mouse.y >= rect.y1 - pad &&
    mouse.y <= rect.y2 + pad
  );
}

// ---- 渲染几何（§3.6.1：窗口内裁切/外推 + extent 求交，纯函数可单测）----

export const DRAWING_COLOR = '#38bdf8'; // sky-400，与 MA 五色/BOLL 灰/MACD 红绿均不撞色
const EXTENT_EPS = 1e-6;

export interface RenderWindow {
  /** 可见窗口类目索引（含端点） */
  startIdx: number;
  endIdx: number;
}

export interface RenderExtent {
  min: number;
  max: number;
}

// 画线锚点日期 → 类目索引：命中即用、未命中 −1（调用方跳过渲染，不做吸附——
// 吸附会移动画线端点致斜率错乱，并可造出 idx1===idx2 除零；口径分工见 §3.6.1）
export function anchorIndex(xAxisData: string[], date: string): number {
  return xAxisData.indexOf(date);
}

// 窗口边缘日期 → 最近类目索引（与 ECharts startValue 吸附最近类目同语义；
// visibleRange 端点可能是非交易日自然日；xAxisData 升序）
export function windowEdgeIndex(xAxisData: string[], date: string): number {
  if (xAxisData.length === 0) return -1;
  const target = Date.parse(`${date}T00:00:00Z`);
  let nearest = 0;
  let best = Infinity;
  for (let i = 0; i < xAxisData.length; i++) {
    const d = Math.abs(Date.parse(`${xAxisData[i]}T00:00:00Z`) - target);
    if (d < best) {
      best = d;
      nearest = i;
    } else if (xAxisData[i] > date) {
      break; // 升序提前退出
    }
  }
  return nearest;
}

// 可见窗口内主图全系列极值并集（ohlc high/low + MA + BOLL 上/中/下轨）。
// 并集恒 ⊆ axis extent（nice 取整只向外扩）——仅作 containData 兜底、不作夹取精度目标
export function unionExtent(
  startIdx: number,
  endIdx: number,
  ohlc: [number, number, number, number][],
  maValues: (number | null)[][],
  bollValues: (number | null)[][],
): RenderExtent | null {
  let min = Infinity;
  let max = -Infinity;
  const visit = (v: number | null) => {
    if (v !== null && Number.isFinite(v)) {
      min = Math.min(min, v);
      max = Math.max(max, v);
    }
  };
  for (let i = startIdx; i <= endIdx; i++) {
    if (ohlc[i]) {
      visit(ohlc[i][2]); // high
      visit(ohlc[i][3]); // low
    }
    for (const arr of maValues) visit(arr[i]);
    for (const arr of bollValues) visit(arr[i]);
  }
  return Number.isFinite(min) ? { min, max } : null;
}

// markLine.data 的每一项本身就是两点数组（嵌套形态）；per-item 样式
// （lineStyle/label/emphasis）必须挂在两点数组的首元素上——两点形式无"项级样式"层，
// 包一层 {data: ...} 对象会走非数组分支、按 undefined.coord 抛 TypeError
export type LineMarkItem = [
  {
    coord: [number, number];
    symbol: 'none';
    lineStyle: { type: 'solid'; width: number; color: string };
    emphasis: { disabled: boolean };
    label: { show: boolean; formatter?: () => string; color?: string };
  },
  { coord: [number, number]; symbol: 'none' },
];

export interface DrawingMarks {
  markLine: LineMarkItem[];
  markPoint: Array<{
    name: string;
    coord: [number, number];
    symbol: string;
    symbolSize: number;
    label: { show: boolean; formatter: () => string; color: string };
  }>;
}

// 单条画线的渲染端点（窗口内裁切/外推 x 先做、extent 求交 y 后做；跳过规则按线型）。
// 渲染（buildDrawingsMarks）与编辑命中检测共用——两者几何必须同源（§3.6.1），
// 否则命中测试的是原始锚点、渲染的是裁切后的线段，射线外推段不可命中/截断段误命中。
// 返回 null = 该线按渲染规则跳过。
export function renderedSegment(
  d: Drawing,
  xAxisData: string[],
  window: RenderWindow,
  extent: RenderExtent,
): { i1: number; p1: number; i2: number; p2: number } | null {
  if (window.startIdx < 0 || window.endIdx < 0 || window.startIdx > window.endIdx) return null;
  if (d.kind === 'text') return null; // text 走 markPoint，无线段
  if (d.kind === 'hline') {
    // 完全越界（几何判定 + eps）→ 跳过渲染
    if (d.p1.price < extent.min - EXTENT_EPS || d.p1.price > extent.max + EXTENT_EPS) return null;
    return { i1: window.startIdx, p1: d.p1.price, i2: window.endIdx, p2: d.p1.price };
  }
  // trend / ray
  const i1 = anchorIndex(xAxisData, d.p1.date);
  const i2 = anchorIndex(xAxisData, d.p2.date);
  if (i1 < 0 || i2 < 0 || i1 === i2) return null; // 未命中/同索引兜底（提交与载入校验已挡，渲染双保险）
  const linePrice = (idx: number) =>
    d.p1.price + ((d.p2.price - d.p1.price) * (idx - i1)) / (i2 - i1);
  const a = Math.min(i1, i2);
  const b = Math.max(i1, i2);
  if (d.kind === 'ray') {
    // 射线：跳过规则 = p1 在窗口右侧（p1→p2 向右延伸，起点 = p1 与窗口左缘中靠右者；
    // 用 p1 自身索引——右→左绘制时 min(i1,i2) 会把 p1 左侧那段误画出来）
    if (i1 > window.endIdx) return null;
  } else if (b < window.startIdx || a > window.endIdx) {
    return null; // trend 两端点均在窗口同一侧（与窗口无交集）
  }
  // x 端点：起点 = 左缘与左锚点中靠右者；终点 = trend 右锚点/右缘、ray 外推到右缘（同式）
  let sIdx = d.kind === 'ray' ? Math.max(i1, window.startIdx) : Math.max(a, window.startIdx);
  let eIdx = d.kind === 'ray' ? window.endIdx : Math.min(b, window.endIdx);
  let sPrice = linePrice(sIdx);
  let ePrice = linePrice(eIdx);
  // y 求交：extent 内端点按原价渲染、仅超界端点求交（保留斜率）；无交集（几何判定 + eps）→ 跳过
  if (
    (sPrice < extent.min - EXTENT_EPS && ePrice < extent.min - EXTENT_EPS) ||
    (sPrice > extent.max + EXTENT_EPS && ePrice > extent.max + EXTENT_EPS)
  ) {
    return null;
  }
  if (sPrice !== ePrice) {
    // 两锚点价格相等 = 水平线段：区间内整段保留（无交集判定已挡全外），跳过 y 求交。
    // 两个交点都必须基于原始线段计算（先算完再赋值，避免前一个交点移动 sIdx 污染后一个）；
    // 参数化基准统一为起点（t = (boundary − origSPrice) / (origEPrice − origSPrice)）
    const origSIdx = sIdx;
    const origEIdx = eIdx;
    const origSPrice = sPrice;
    const origEPrice = ePrice;
    const tOf = (boundary: number) => (boundary - origSPrice) / (origEPrice - origSPrice);
    const sT = origSPrice < extent.min - EXTENT_EPS ? tOf(extent.min) : origSPrice > extent.max + EXTENT_EPS ? tOf(extent.max) : null;
    const eT = origEPrice < extent.min - EXTENT_EPS ? tOf(extent.min) : origEPrice > extent.max + EXTENT_EPS ? tOf(extent.max) : null;
    if (sT !== null) {
      sIdx = Math.round(origSIdx + sT * (origEIdx - origSIdx));
      // 落位取 extent 边界值本身（实测边界严格包含：恰等于边界出图、+1e-9 整条丢弃）
      sPrice = origSPrice < extent.min - EXTENT_EPS ? extent.min : extent.max;
    }
    if (eT !== null) {
      eIdx = Math.round(origSIdx + eT * (origEIdx - origSIdx));
      ePrice = origEPrice < extent.min - EXTENT_EPS ? extent.min : extent.max;
    }
  }
  if (sIdx === eIdx) return null; // 求交后退化
  return { i1: sIdx, p1: sPrice, i2: eIdx, p2: ePrice };
}

// option 构建与渲染后 merge 修正共用的构建函数（§3.6.1 读取时序：两条路径一个构建函数——
// 选中态 width 3、label formatter、样式覆盖全部由它产出，否则 merge 会抹掉选中高亮）。
export function buildDrawingsMarks(
  drawings: Drawing[],
  xAxisData: string[],
  window: RenderWindow,
  extent: RenderExtent,
  selectedId: string | null,
): DrawingMarks {
  const markLine: LineMarkItem[] = [];
  const markPoint: DrawingMarks['markPoint'] = [];
  for (const d of drawings) {
    if (d.kind === 'text') {
      const idx = anchorIndex(xAxisData, d.pos.date);
      if (idx < 0) continue; // 锚点未命中 → 跳过渲染不删数据（§3.6.1）
      // 锚点价格超出 extent → 不渲染（不裁剪——点无法裁剪，与 markLine 求交口径不同）
      if (d.pos.price < extent.min - EXTENT_EPS || d.pos.price > extent.max + EXTENT_EPS) continue;
      markPoint.push({
        name: d.id,
        coord: [idx, d.pos.price],
        symbol: 'circle',
        symbolSize: 0,
        label: { show: true, formatter: () => d.text, color: DRAWING_COLOR },
      });
      continue;
    }
    const seg = renderedSegment(d, xAxisData, window, extent);
    if (!seg) continue;
    const labelFn =
      d.kind === 'hline' ? () => d.label ?? d.p1.price.toFixed(2) : undefined;
    markLine.push(lineMark(selectedId === d.id, seg.i1, seg.p1, seg.i2, seg.p2, labelFn));
  }
  return { markLine, markPoint };
}

function lineMark(
  selected: boolean,
  i1: number,
  p1: number,
  i2: number,
  p2: number,
  labelFn?: () => string,
): LineMarkItem {
  // 嵌套两点形态（实测扁平 [{coord},{coord}] 形态 setOption 抛 TypeError，必须嵌套一层）；
  // per-item 样式（lineStyle/label/emphasis）挂在两点数组首元素上——包一层 {data:...}
  // 对象会被 markLineFilter 按 undefined.coord 抛 TypeError
  return [
    {
      coord: [i1, p1],
      symbol: 'none', // 两端点无符号
      lineStyle: { type: 'solid', width: selected ? 3 : 1.5, color: DRAWING_COLOR },
      emphasis: { disabled: true }, // 关闭默认 hover 加粗，选中态 width 3 成为唯一加粗来源
      label: labelFn
        ? { show: true, formatter: labelFn, color: DRAWING_COLOR } // 函数 formatter：避模板语义串味
        : { show: false },
    },
    { coord: [i2, p2], symbol: 'none' },
  ];
}
