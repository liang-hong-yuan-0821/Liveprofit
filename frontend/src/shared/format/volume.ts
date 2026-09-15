// 成交量展示格式化（§3.7 读条）：按量级自适应缩写（手 → 万手/亿手；上游口径 vol 单位 = 手）

export function formatVolume(vol: number | null | undefined): string {
  if (vol === null || vol === undefined || !Number.isFinite(vol)) return '—';
  const abs = Math.abs(vol);
  if (abs >= 1e8) return `${(vol / 1e8).toFixed(2)}亿手`;
  if (abs >= 1e4) return `${(vol / 1e4).toFixed(2)}万手`;
  return `${vol.toFixed(0)}手`;
}
