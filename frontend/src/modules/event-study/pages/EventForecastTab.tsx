import { useEffect, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import {
  useDailyResearchRun,
  useDailyResearchRuns,
  useLatestEventResearchRun,
} from '../../daily-research/pages/queries';

type Row = Record<string, any>;
const directionLabel: Record<string, string> = {
  bullish: '偏利多', bearish: '偏利空', mixed: '多空分化', neutral: '中性', unknown: '方向未知',
};
const stageLabel: Record<string, string> = {
  rumor: '传闻', published: '已公布', announced: '已公布', pending: '待实施', implemented: '已实施', cancelled: '已取消', unknown: '证据不足',
};
const outcomeLabel: Record<string, string> = {
  validated: '已验证', partial: '部分期限已验证', awaiting_window: '等待行情窗口成熟',
  awaiting_event_session: '等待事件交易日', data_insufficient: '行情数据不足', unavailable: '验证不可用',
};
const directionLabelActual: Record<string, string> = {
  bullish: '偏利多', bearish: '偏利空', neutral: '中性',
};

function formatTime(value?: string | null) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
}

function formatPercent(value?: number | null) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return `${(Number(value) * 100).toFixed(2)}%`;
}

export function EventForecastTab() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runIdParam = searchParams.get('run_id');
  const eventId = searchParams.get('event_id');
  const runsQuery = useDailyResearchRuns('news');
  const runs = runsQuery.data ?? [];
  const eventLookup = useLatestEventResearchRun(
    eventId && /^\d+$/.test(eventId) ? Number(eventId) : null,
  );
  const matchingRun = eventLookup.data?.run;
  const selectedRun = runs.find((run) => run.task_id === runIdParam)
    ?? (runIdParam && matchingRun?.task_id === runIdParam ? matchingRun : undefined)
    ?? (eventId ? matchingRun : runs[0]);
  const displayRuns = matchingRun && !runs.some((run) => run.task_id === matchingRun.task_id)
    ? [matchingRun, ...runs]
    : runs;
  const detailQuery = useDailyResearchRun(selectedRun?.task_id ?? null);
  const report = detailQuery.data?.report as Row | null | undefined;
  const events: Row[] = report?.event_forecast ?? [];
  const selectedEvent = events.find((event) => String(event.event_id) === eventId) ?? null;
  const outlook = report?.market_outlook?.horizons ?? {};
  const horizonEntries = useMemo(() => Object.entries(outlook) as [string, Row][], [outlook]);

  useEffect(() => {
    if (eventId && !runIdParam) {
      if (matchingRun) {
        const params = new URLSearchParams(searchParams);
        params.set('run_id', matchingRun.task_id);
        setSearchParams(params, { replace: true });
      }
      return;
    }
    if (!runs.length) return;
    if (!runIdParam || (!runs.some((run) => run.task_id === runIdParam)
        && matchingRun?.task_id !== runIdParam)) {
      const params = new URLSearchParams(searchParams);
      params.set('run_id', runs[0].task_id);
      setSearchParams(params, { replace: true });
    }
  }, [runs, runIdParam, eventId, matchingRun?.task_id, searchParams, setSearchParams]);

  function chooseRun(taskId: string) {
    const params = new URLSearchParams(searchParams);
    params.set('run_id', taskId);
    params.delete('event_id');
    setSearchParams(params, { replace: true });
  }

  function chooseEvent(event: Row) {
    const params = new URLSearchParams(searchParams);
    params.set('event_id', String(event.event_id));
    setSearchParams(params, { replace: true });
  }

  if (runsQuery.isPending) return <LoadingState label="读取事件研究批次…" />;
  if (runsQuery.isError) return <ErrorState error={runsQuery.error} onRetry={() => void runsQuery.refetch()} />;
  if (runs.length === 0 && !matchingRun) {
    if (eventId && eventLookup.isPending) return <LoadingState label="查找包含该事件的历史批次…" />;
    return <Card><CardContent className="flex flex-col gap-3 py-8 text-sm">
      <p>{eventId ? `事件 #${eventId} 尚未出现在每日事件研究批次中。` : '尚无新闻研究批次。系统会在每天 09:00 和 21:00 自动分析新闻。'}</p>
      <Link to="/daily-research" className="text-[var(--color-accent-bright)] underline">前往每日研究手动开始分析</Link>
    </CardContent></Card>;
  }

  return (
    <section className="flex flex-col gap-4">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
          <div>
            <p className="font-medium">按研究时点查看冻结的事件预测</p>
            <p className="mt-1 text-xs text-[var(--color-fg-muted)]">历史批次使用当时纳入的判断版本，不会被后续更正覆盖。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <select
              aria-label="研究批次"
              className="h-9 max-w-[320px] rounded-md border bg-transparent px-3 text-sm"
              style={{ borderColor: 'var(--color-border)' }}
              value={selectedRun?.task_id ?? ''}
              onChange={(event) => chooseRun(event.target.value)}
            >
              {displayRuns.map((run) => <option key={run.task_id} value={run.task_id}>
                {run.slot ?? (run.trigger === 'manual' ? '手动' : '定时')} · {formatTime(run.created_at)} · {run.report_status ?? run.status}
              </option>)}
            </select>
            <Link to="/daily-research" className="text-sm text-[var(--color-accent-bright)] underline">研究总览</Link>
          </div>
        </CardContent>
      </Card>

      {detailQuery.isPending && <LoadingState label="载入该批次事件与展望…" />}
      {detailQuery.isError && <ErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />}
      {eventId && !selectedRun && !eventLookup.isPending && <p className="text-sm text-amber-400">没有找到包含该事件的每日研究批次。</p>}
      {report && (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            {horizonEntries.map(([days, value]) => (
              <Card key={days}>
                <CardContent className="py-4">
                  <p className="text-xs text-[var(--color-fg-muted)]">{days} 个交易日 · {value.date_range?.start ?? '日期待定'} 至 {value.date_range?.end ?? '日期待定'}</p>
                  <p className="mt-2 font-semibold">{directionLabel[value.direction] ?? value.direction}</p>
                  <p className="mt-1 text-xs text-[var(--color-fg-muted)]">证据置信等级 {value.confidence ?? 'low'}</p>
                  {(value.invalidations ?? []).map((item: string) => <p key={item} className="mt-1 text-xs text-[var(--color-fg-muted)]">失效条件：{item}</p>)}
                  {(value.drivers ?? []).slice(0, 2).map((driver: Row) => <p key={driver.assessment_id} className="mt-2 truncate text-xs">{driver.title}</p>)}
                </CardContent>
              </Card>
            ))}
          </div>
          {selectedRun && (
            <p className="text-xs text-[var(--color-fg-muted)]">
              批次 {selectedRun.slot ?? (selectedRun.trigger === 'manual' ? '手动分析' : '定时分析')} · 新闻截止 {formatTime(report.news_cutoff_at)} · 报告时点 {formatTime(report.report_as_of)} · 状态 {report.status}
            </p>
          )}
          {report.outcome_validation_status && <p className="text-xs text-[var(--color-fg-muted)]">实际行情验证：{outcomeLabel[report.outcome_validation_status] ?? (report.outcome_validation_status === 'available' ? '已读取行情快照' : report.outcome_validation_status)} · 截止交易日 {report.market_as_of_trade_date ?? '—'}</p>}
          <div className="grid gap-4 lg:grid-cols-[minmax(280px,0.9fr)_minmax(0,1.5fr)]">
            <Card>
              <CardHeader><CardTitle>本版事件 · {events.length}</CardTitle></CardHeader>
              <CardContent className="max-h-[70vh] overflow-y-auto">
                {events.length === 0 && <p className="text-sm text-[var(--color-fg-muted)]">本批次没有可列出的事件判断。</p>}
                <div className="flex flex-col gap-2">
                  {events.map((event) => (
                    <button
                      key={event.assessment_id}
                      type="button"
                      onClick={() => chooseEvent(event)}
                      aria-pressed={String(event.event_id) === eventId}
                      className="rounded-lg border p-3 text-left text-sm aria-pressed:border-[var(--color-accent-bright)]"
                      style={{ borderColor: 'var(--color-border)' }}
                    >
                      <span className="block font-medium">{event.title}</span>
                      <span className="mt-1 block text-xs text-[var(--color-fg-muted)]">{stageLabel[event.stage] ?? event.stage} · {formatTime(event.available_at)}</span>
                      <span className="mt-2 block text-xs">{(event.targets ?? []).flatMap((target: Row) => (target.horizons ?? []).map((h: Row) => `${h.trading_days}日 ${directionLabel[h.direction] ?? h.direction}`)).join(' · ')}</span>
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>事实阶段与市场兑现</CardTitle></CardHeader>
              <CardContent>
                {!selectedEvent && <p className="text-sm text-[var(--color-fg-muted)]">从左侧选择本批次事件以查看证据与判断。</p>}
                {eventId && !selectedEvent && <p className="text-sm text-amber-400">事件 #{eventId} 不属于当前批次。可从上方切换到包含该事件的历史批次。</p>}
                {selectedEvent && <EventDetail event={selectedEvent} />}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </section>
  );
}

function EventDetail({ event }: { event: Row }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold">{event.title}</h2>
        <p className="mt-1 text-xs text-[var(--color-fg-muted)]">事件 #{event.event_id} · 判断版本 {event.revision} · 可用时间 {formatTime(event.available_at)}</p>
      </div>
      <dl className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
        <div><dt className="text-xs text-[var(--color-fg-muted)]">事实阶段</dt><dd className="mt-1 text-sm">{stageLabel[event.stage] ?? event.stage ?? '证据不足'}{event.validity_status === 'expired' ? ' · 已过有效期' : ''}</dd></div>
        <div><dt className="text-xs text-[var(--color-fg-muted)]">验证状态</dt><dd className="mt-1 text-sm">{outcomeLabel[event.status] ?? event.status}</dd></div>
        <div><dt className="text-xs text-[var(--color-fg-muted)]">预期值</dt><dd className="mt-1 text-sm">{event.expected_value ?? '—'}</dd></div>
        <div><dt className="text-xs text-[var(--color-fg-muted)]">实际值 / 前值</dt><dd className="mt-1 text-sm">{event.actual_value ?? '—'} / {event.previous_value ?? '—'}</dd></div>
      </dl>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">受影响对象与期限判断</h3>
        {(event.targets ?? []).map((target: Row, index: number) => (
          <div key={`${target.target}-${index}`} className="rounded-lg border p-3" style={{ borderColor: 'var(--color-border)' }}>
            <p className="text-sm font-medium">{target.target ?? target.scope}</p>
            <p className="mt-1 text-xs text-[var(--color-fg-muted)]">落地验证：{outcomeLabel[target.outcome_status] ?? target.outcome_status ?? '未运行'}{target.outcome_reason ? ` · ${target.outcome_reason}` : ''}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {(target.horizons ?? []).map((horizon: Row) => {
                const validation = horizon.validation ?? {};
                return <div key={horizon.trading_days} className="min-w-[210px] flex-1 rounded-md bg-[var(--color-surface-raised)] p-2 text-xs">
                  <p className="font-medium">{horizon.trading_days} 日 · {directionLabel[horizon.direction] ?? horizon.direction} · 强度 {horizon.strength ?? '—'} · 置信 {horizon.confidence ?? '—'}</p>
                  <p className="mt-1 text-[var(--color-fg-muted)]">实际：{outcomeLabel[validation.status] ?? '尚无验证'}{validation.actual_direction ? ` · ${directionLabelActual[validation.actual_direction] ?? validation.actual_direction}` : ''}</p>
                  {validation.status === 'validated' && <p className="mt-1 text-[var(--color-fg-muted)]">区间 {validation.start_date}—{validation.end_date} · 对象 {formatPercent(validation.target_return)} · 基准 {formatPercent(validation.benchmark_return)} · 超额 {formatPercent(validation.abnormal_return)}</p>}
                  {validation.status === 'validated' && <p className="mt-1">预测{validation.prediction_match == null ? '暂无可比方向' : validation.prediction_match ? '与实际方向一致' : '与实际方向不一致'}</p>}
                  {validation.reason && <p className="mt-1 text-[var(--color-fg-muted)]">{validation.reason}</p>}
                  {validation.status !== 'validated' && validation.end_date && <p className="mt-1 text-[var(--color-fg-muted)]">目标区间结束：{validation.end_date}</p>}
                </div>;
              })}
            </div>
            {target.reason && <p className="mt-2 text-xs text-[var(--color-fg-muted)]">{target.reason}</p>}
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">来源证据</h3>
        {(event.evidence ?? []).map((evidence: Row, index: number) => <blockquote key={`${evidence.news_id ?? ''}-${index}`} className="border-l-2 pl-3 text-sm" style={{ borderColor: 'var(--color-border)' }}>
          <p>{evidence.quote ?? evidence.text ?? '未提供原文摘录'}</p>
          <p className="mt-1 text-xs text-[var(--color-fg-muted)]">{evidence.source ?? evidence.news_id ?? '来源记录'}{evidence.url ? ` · ${evidence.url}` : ''}</p>
        </blockquote>)}
      </div>
      <p className="text-xs text-[var(--color-fg-muted)]">价格变化用于描述落地后的市场反应，不单独证明因果。个股使用复权收益，板块因缺少历史成分快照会标记数据不足；窗口未结束或行情缺失时不缩短区间冒充验证。</p>
    </div>
  );
}
