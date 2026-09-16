// 画线数据模块（§3.6）：类型 + localStorage 读写 + 校验 + 命中检测纯函数。
// 锚点存"日期 + 价格 + 小数 x"数据坐标（非像素）：dataZoom 缩放平移与渐进加载
// 前插数据（只前插不移除）时画线天然跟随、不漂移。x = 类目小数索引（绘制时
// mousedown/mouseup 的连续位置，线起点精确落在按点像素处）；旧数据/编辑拖拽无 x →
// 回落 bar 中心整数索引（原行为）。

export type DrawingAnchor = { date: string; price: number; x?: number };

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
  // x 可选（旧数据无 x）；有则必须为有限数（渲染时夹到所在 bar 带内，见 renderedSegment）
  return (
    isValidDate(a.date) &&
    isFiniteNumber(a.price) &&
    (a.x === undefined || isFiniteNumber(a.x))
  );
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
export const EXTENT_EPS = 1e-6;

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

// 画线渲染改为 zr 图元直挂（§3.6：类目轴 markLine 只支持整数索引坐标，
// 小数锚点 x 无法经 markLine 渲染——convertToPixel 对小数索引取整到 bar 中心，实测）。
// 渲染几何纯函数仍由 renderedSegment 承担，像素换算在组件内（需运行时轴信息）。

// 单条画线的渲染端点（窗口内裁切/外推 x 先做、extent 求交 y 后做；跳过规则按线型）。
// 渲染（zr 图元）与编辑命中检测共用——两者几何必须同源（§3.6.1），
// 否则命中测试的是原始锚点、渲染的是裁切后的线段，射线外推段不可命中/截断段误命中。
// 端点索引可为小数（锚点 x 的连续位置）：窗口裁切边界取半开带（startIdx−0.5/endIdx+0.5，
// 与网格左右缘同像素），带内小数端点原样保留——绘制起点精确落在按点位置。
// 返回 null = 该线按渲染规则跳过。
export function renderedSegment(
  d: Drawing,
  xAxisData: string[],
  window: RenderWindow,
  extent: RenderExtent,
): { i1: number; p1: number; i2: number; p2: number } | null {
  if (window.startIdx < 0 || window.endIdx < 0 || window.startIdx > window.endIdx) return null;
  if (d.kind === 'text') return null; // text 走文本图元，无线段
  if (d.kind === 'hline') {
    // 完全越界（几何判定 + eps）→ 跳过渲染；两端 = 窗口半开带边缘（网格左右缘同像素）
    if (d.p1.price < extent.min - EXTENT_EPS || d.p1.price > extent.max + EXTENT_EPS) return null;
    return { i1: window.startIdx - 0.5, p1: d.p1.price, i2: window.endIdx + 0.5, p2: d.p1.price };
  }
  // trend / ray
  const i1 = anchorIndex(xAxisData, d.p1.date);
  const i2 = anchorIndex(xAxisData, d.p2.date);
  if (i1 < 0 || i2 < 0 || i1 === i2) return null; // 未命中/同索引兜底（提交与载入校验已挡，渲染双保险）
  // 小数 x（锚点连续位置）；无 x（旧数据/编辑拖拽重置）回落 bar 中心整数索引；
  // 夹到所在 bar 的带内 [i−0.5, i+0.5]（localStorage 手改防御）
  const xOf = (i: number, x: number | undefined) =>
    x === undefined ? i : Math.max(i - 0.5, Math.min(i + 0.5, x));
  let x1 = xOf(i1, d.p1.x);
  let x2 = xOf(i2, d.p2.x);
  if (x1 === x2) {
    x1 = i1;
    x2 = i2; // 相邻 bar 都夹到同一边界（手改数据）→ 退化回中心，防除零
  }
  const linePrice = (idx: number) =>
    d.p1.price + ((d.p2.price - d.p1.price) * (idx - x1)) / (x2 - x1);
  const a = Math.min(x1, x2);
  const b = Math.max(x1, x2);
  if (d.kind === 'ray') {
    // 射线：跳过规则 = p1 在窗口右侧（p1→p2 向右延伸，起点 = p1 与窗口左缘中靠右者；
    // 用 p1 自身索引——右→左绘制时 min(i1,i2) 会把 p1 左侧那段误画出来）
    if (i1 > window.endIdx) return null;
  } else if (b < window.startIdx || a > window.endIdx) {
    return null; // trend 两端点均在窗口同一侧（与窗口无交集）
  }
  // x 端点：起点 = 左缘与左锚点中靠右者；终点 = trend 右锚点/右缘、ray 外推到右缘（同式）
  let sIdx = d.kind === 'ray' ? Math.max(x1, window.startIdx - 0.5) : Math.max(a, window.startIdx - 0.5);
  let eIdx = d.kind === 'ray' ? window.endIdx + 0.5 : Math.min(b, window.endIdx + 0.5);
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
      // 交点索引保留小数（与小数锚点同粒度，像素级精确；原 Math.round 是为整数类目索引）
      sIdx = origSIdx + sT * (origEIdx - origSIdx);
      // 落位取 extent 边界值本身（实测边界严格包含：恰等于边界出图、+1e-9 整条丢弃）
      sPrice = origSPrice < extent.min - EXTENT_EPS ? extent.min : extent.max;
    }
    if (eT !== null) {
      eIdx = origSIdx + eT * (origEIdx - origSIdx);
      ePrice = origEPrice < extent.min - EXTENT_EPS ? extent.min : extent.max;
    }
  }
  if (sIdx === eIdx) return null; // 求交后退化
  return { i1: sIdx, p1: sPrice, i2: eIdx, p2: ePrice };
}

