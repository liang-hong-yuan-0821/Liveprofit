import { Resource, type RefreshGroup } from '../../../api/generated';
import type { useMarketRefresh } from '../pages/refreshQueries';

const names: Record<Resource, string> = {
  CN_INDEX_BARS: '中国指数', CN_INDEX_FACTORS: '指数指标', US_INDEX_BARS: '美国指数',
  KR_INDEX_BARS: '韩国指数', CN_STOCK_DAILY: '个股日线', CN_SECTOR_DAILY: '板块日线',
};
const activeJobs = new Set(['QUEUED', 'RUNNING', 'RETRY_WAIT']);

function progress(group: RefreshGroup) {
  const job = group.job;
  if (!job || !activeJobs.has(job.status)) return null;
  if (job.status === 'QUEUED') return '排队中';
  if (job.status === 'RETRY_WAIT') return `等待重试 · 已处理 ${job.processed}/${job.total}`;
  return `拉取中 · 已处理 ${job.processed}/${job.total}`;
}

export function MarketRefreshStatus({ refresh, resources }: {
  refresh: ReturnType<typeof useMarketRefresh>;
  resources: readonly Resource[];
}) {
  return <div aria-label="行情拉取状态" className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-fg-muted)]">
    {resources.map(resource => {
      const group = refresh.status?.groups.find(item => item.resource === resource);
      const active = group && progress(group);
      const covered = group ? group.available_count + group.exempt_count : 0;
      const healthy = group?.freshness === 'FRESH';
      return <div key={resource} className="inline-flex min-h-7 flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
        <span className="font-medium text-[var(--color-fg)]">{names[resource]}</span>
        {!group && <span>{refresh.error ? '状态暂不可用' : '正在核验'}</span>}
        {group && <>
          <span className={active ? 'text-[var(--color-accent-bright)]' : healthy ? '' : 'text-amber-400'}>
            {active ?? (healthy ? '已更新' : `待补齐 ${covered}/${group.expected_count}`)}
          </span>
          {group.expected_trade_date && <span>· {group.expected_trade_date}</span>}
          {refresh.error && <span>· 上次核验，当前状态暂不可用</span>}
          {!healthy && group.manual_eligibility.allowed &&
            <button type="button" className="font-medium text-[var(--color-accent-bright)] underline-offset-2 hover:underline disabled:opacity-50" disabled={refresh.pending || refresh.error || !refresh.status?.refresh_available || !refresh.status.worker_online} onClick={() => refresh.retry(resource)}>重试</button>}
          {!healthy && group.manual_eligibility.reason === 'CATALOG_INCOMPLETE' &&
            <span>· 股票目录缺少上市资料</span>}
          {!healthy && group.warnings?.includes('HISTORY_GAP') &&
            <span>· 较早历史仍有缺口</span>}
        </>}
      </div>;
    })}
  </div>;
}
