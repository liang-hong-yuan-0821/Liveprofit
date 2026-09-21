import { useMemo, useState } from 'react';
import { toApiError } from '../../../api/client';
import type { TrendSeriesDTO, TrendsData } from '../../../api/generated';
import { LineChart } from '../../../shared/charts/LineChart';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { todayLocalDate } from '../../../shared/format/dateTime';
import { Badge } from '../../../shared/ui/badge';
import { toTrendChartSeries } from './mappers/toTrendViewModels';
import { useBoardTrendsQuery, useCapTierTrendsQuery, type TrendsFilters } from './queries';

// 趋势对比区块（趋势对比面板方案 4.4）：市值分层 / 市场板 两 Tab + 区间选择器。
// - Tab = 按钮组 aria-pressed（不得 role="tab"——页面测试断言 queryByRole('tab') 为空，
//   且 shared/ui 无 Tabs 组件）
// - 区间口径写死：to 一律 = 今天；from = addMonths(今天,-N)（日溢出夹取到目标月最后
//   一天，不得 setMonth 裸调——会把 1/31 滚成 3/3）；「全部」= 1990-01-01（早于任何
//   库内数据）。区间边界只决定取多少历史：归一按共同首日裁掉基点日之前的点，故
//   几天级的日期漂移不影响曲线正确性
// - 序列名/颜色取响应 series[].name（前端不硬编码指数名单）；图注固定口径说明
// - as_of/freshness_status 消费：图表下方右侧「数据截至 {as_of}」（as_of=null 整条
//   省略）；STALE → 「数据滞后」角标；UNAVAILABLE → 「暂无数据」角标（面板级文案，
//   与逐序列「无数据：<name>」刻意区分）
// - 空序列 → "无数据：<name>"角标（序列条目由服务端恒返回，契约 4.2.1 第 4 条）
// - 加载提示绝对定位 overlay 不挤占布局（CLAUDE.md 页面重排规避）；图表容器固定 420px

type TabKey = 'cap-tiers' | 'boards';
type RangeKey = '3m' | '1y' | '3y' | 'all';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'cap-tiers', label: '市值分层' },
  { key: 'boards', label: '市场板' },
];

const RANGES: { key: RangeKey; label: string; months: number | null }[] = [
  { key: '3m', label: '近3月', months: 3 },
  { key: '1y', label: '近1年', months: 12 },
  { key: '3y', label: '近3年', months: 36 },
  { key: 'all', label: '全部', months: null },
];

const ALL_RANGE_FROM = '1990-01-01'; // 早于任何库内数据（上证综指首行 1990-12-19）

// 图注固定口径说明（口径元数据不承载在响应字段组，见 TrendsData docstring）
const CAP_TIER_NOTE = '市值分层：指数收盘归一（共同首日=100）';
const BOARD_NOTE = '市场板：上证综指含科创板（2020-07-22 修订）、创业板指 100 只样本、科创50 50 只样本';

const CHART_HEIGHT = 420;

/** addMonths：目标月同日，日溢出时夹取到目标月最后一天
 * （2026-03-31 减 1 月 → 2026-02-28；不得用 setMonth 裸调）。 */
export function addMonthsClamped(dateStr: string, months: number): string {
  const [year, month, day] = dateStr.split('-').map(Number);
  const total = year * 12 + (month - 1) + months;
  const targetYear = Math.floor(total / 12);
  const targetMonth = total % 12; // 0–11
  const lastDay = new Date(targetYear, targetMonth + 1, 0).getDate();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${targetYear}-${pad(targetMonth + 1)}-${pad(Math.min(day, lastDay))}`;
}

/** 区间选项 → from 日期（to 一律 = 今天）。 */
export function trendRangeFrom(key: RangeKey, today: string): string {
  const months = RANGES.find((r) => r.key === key)?.months;
  return months === null || months === undefined
    ? ALL_RANGE_FROM
    : addMonthsClamped(today, -months);
}

export function TrendComparisonPanel() {
  const [tab, setTab] = useState<TabKey>('cap-tiers');
  const [range, setRange] = useState<RangeKey>('1y');
  const today = todayLocalDate();
  const filters: TrendsFilters = { from: trendRangeFrom(range, today), to: today };

  const capQuery = useCapTierTrendsQuery(filters);
  const boardQuery = useBoardTrendsQuery(filters);
  const query = tab === 'cap-tiers' ? capQuery : boardQuery;
  const note = tab === 'cap-tiers' ? CAP_TIER_NOTE : BOARD_NOTE;

  const data: TrendsData | undefined = query.data;
  const series: TrendSeriesDTO[] = data?.series ?? [];
  const emptyNames = series.filter((s) => s.points.length === 0).map((s) => s.name);
  const allEmpty = series.length > 0 && series.every((s) => s.points.length === 0);
  const stale = data?.freshness_status === 'STALE';
  const unavailable = data?.freshness_status === 'UNAVAILABLE';
  // model 引用稳定（series 取自查询数据）：LineChart 内核 memo 拦截重渲染生效
  const chartModel = useMemo(() => ({ series: toTrendChartSeries(series) }), [series]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex gap-1" role="group" aria-label="趋势对比 Tab">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              aria-pressed={tab === t.key}
              className="rounded border px-3 py-1 text-sm"
              style={
                tab === t.key
                  ? { borderColor: 'var(--color-accent)', color: 'var(--color-accent)' }
                  : { borderColor: 'var(--color-border)' }
              }
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1" role="group" aria-label="趋势区间选择">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              aria-pressed={range === r.key}
              className="rounded border px-3 py-1 text-sm"
              style={
                range === r.key
                  ? { borderColor: 'var(--color-accent)', color: 'var(--color-accent)' }
                  : { borderColor: 'var(--color-border)' }
              }
              onClick={() => setRange(r.key)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
        {note}
      </p>

      {/* 固定高度容器：加载 overlay 绝对定位、不挤占布局（页面重排规避规则） */}
      <div style={{ position: 'relative', height: CHART_HEIGHT }}>
        {query.isPending && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 1,
            }}
          >
            <LoadingState label="趋势加载中…" />
          </div>
        )}
        {query.isError && !query.data && (
          <ErrorState
            error={toApiError(query.error)}
            onRetry={toApiError(query.error).retryable ? () => void query.refetch() : undefined}
          />
        )}
        {data && allEmpty && <EmptyState title="当前区间无趋势数据" />}
        {data && !allEmpty && <LineChart model={chartModel} height={CHART_HEIGHT} />}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {emptyNames.map((name) => (
            <Badge key={name} variant="warning">
              无数据：{name}
            </Badge>
          ))}
        </div>
        <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          {data && data.as_of && <span>数据截至 {data.as_of}</span>}
          {stale && <Badge variant="warning">数据滞后</Badge>}
          {unavailable && <Badge variant="warning">暂无数据</Badge>}
        </div>
      </div>
    </div>
  );
}
