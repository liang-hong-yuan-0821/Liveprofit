// 展示格式化（仅展示层使用；请求/响应时间语义一律以服务端 RFC 3339 为准，不做时区换算）
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function todayLocalDate(): string {
  // 本地日历日（YYYY-MM-DD），用于表单默认"请求交易日=今天"
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function daysAgoLocalDate(days: number): string {
  // 本地日历日往前推 N 天（YYYY-MM-DD），用于市场默认区间"近 N 个自然日"
  const date = new Date();
  date.setDate(date.getDate() - days);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function daysBetween(a: string, b: string): number {
  // 两个 YYYY-MM-DD 的自然日差（b − a，可为负）；纯日历日口径，不做时区换算
  const toMs = (s: string) => Date.parse(`${s}T00:00:00Z`);
  return Math.round((toMs(b) - toMs(a)) / 86_400_000);
}
