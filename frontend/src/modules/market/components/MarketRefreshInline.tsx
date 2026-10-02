import { Resource, type RefreshGroup } from '../../../api/generated';
import type { useMarketRefresh } from '../pages/refreshQueries';

// 区块标题旁的行内行情状态（2026-10-02 用户拍板）：替代大盘页原独立状态块。
// 已更新到目标交易日 = 只显示目标日 MM.DD；未更新且任务在跑 = 「MM.DD 更新中（x%）」；
// 排队 = 「MM.DD 排队中」；无任务未达标 = 「MM.DD 待补齐 x/y」+ 重试。
// 日期取 expected_trade_date（目标交易日），不是库内最新日——只用浏览器本地日期推断会更糟。

/** 两资源并排时（中国行 指数/指标）与大盘 scope 短名；单资源行不显示名字（标题已表达） */
const SHORT_NAMES: Record<Resource, string> = {
  CN_INDEX_BARS: '指数', CN_INDEX_FACTORS: '指标', US_INDEX_BARS: '美国指数',
  KR_INDEX_BARS: '韩国指数', CN_STOCK_DAILY: '个股日线', CN_SECTOR_DAILY: '板块日线',
};

const ACTIVE_JOB_STATUSES = new Set(['QUEUED', 'RUNNING', 'RETRY_WAIT']);

export type MarketRefreshState = Pick<
  ReturnType<typeof useMarketRefresh>, 'status' | 'error' | 'pending' | 'retry'
>;

/** ISO 日期 → MM.DD；缺失/畸形返回 null（不猜日期） */
export function formatTargetDate(value: string | null | undefined): string | null {
  const parts = value?.split('-');
  return parts && parts.length === 3 ? `${parts[1]}.${parts[2]}` : null;
}

/** 目标日达成后的进度文案：'' = 已更新（只显示日期），null = 无任务未达标（显示待补齐） */
function progressText(group: RefreshGroup): string | null {
  const job = group.job;
  if (!job || !ACTIVE_JOB_STATUSES.has(job.status)) {
    return group.freshness === 'FRESH' ? '' : null;
  }
  if (job.status === 'QUEUED') return '排队中';
  if (job.total > 0) return `更新中（${Math.round((job.processed / job.total) * 100)}%）`;
  return `拉取中 ${job.processed}/${job.total}`;
}

export function MarketRefreshInline({ refresh, resources, named = false }: {
  refresh: MarketRefreshState;
  resources: readonly Resource[];
  /** 单资源行是否也带短名（「板块日线 09.30」）；多资源行始终带名 */
  named?: boolean;
}) {
  const labelled = named || resources.length > 1;
  return (
    <span
      aria-label="行情拉取状态"
      className="inline-flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs font-normal text-[var(--color-fg-muted)]"
    >
      {resources.map((resource, index) => {
        const group = refresh.status?.groups.find((item) => item.resource === resource);
        const active = group && progressText(group) !== null;
        const covered = group ? group.available_count + group.exempt_count : 0;
        const healthy = group?.freshness === 'FRESH';
        const target = formatTargetDate(group?.expected_trade_date);
        return (
          // 分隔与状态间用显式空格（不靠 flex gap）：可访问文本读作「指数 09.30 · 指标 09.30」
          <span key={resource} className="whitespace-nowrap">
            {index > 0 && <span aria-hidden="true">·{' '}</span>}
            {labelled && <>{SHORT_NAMES[resource]}{' '}</>}
            {!group && <span>{refresh.error ? '状态暂不可用' : '正在核验'}</span>}
            {group && <>
              {target && <><span className="tabular-nums">{target}</span>{' '}</>}
              <span className={
                active ? 'text-[var(--color-accent-bright)]' : healthy ? '' : 'text-amber-400'
              }>
                {active ? progressText(group)
                  : healthy ? (target ? null : '已更新')
                  : `待补齐 ${covered}/${group.expected_count}`}
              </span>
              {refresh.error && <><span aria-hidden="true">{' '}·{' '}</span><span>上次核验</span></>}
              {!healthy && group.manual_eligibility.allowed &&
                <>{' '}<button
                  type="button"
                  className="font-medium text-[var(--color-accent-bright)] underline-offset-2 hover:underline disabled:opacity-50"
                  disabled={refresh.pending || refresh.error || !refresh.status?.refresh_available || !refresh.status.worker_online}
                  onClick={() => refresh.retry(resource)}
                >重试</button></>}
              {/* HISTORY_GAP 是较早历史缺口，不能读成当前目标日未更新（knowledge/frontend §八） */}
              {!healthy && group.warnings?.includes('HISTORY_GAP') &&
                <><span aria-hidden="true">{' '}·{' '}</span><span>较早历史缺口</span></>}
            </>}
          </span>
        );
      })}
    </span>
  );
}
