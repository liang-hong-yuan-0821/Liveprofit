import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Button } from '../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import { MarkdownView } from '../../../shared/ui/markdown';
import { SectionTabs } from '../../../shared/ui/SectionTabs';
import { Resource } from '../../../api/generated';
import { MarketRefreshStatus } from '../../market/components/MarketRefreshStatus';
import { useMarketRefresh } from '../../market/pages/refreshQueries';
import type { AssessmentReviewRequest } from '../../../api/generated';
import {
  useDailyResearchRun,
  useDailyResearchRuns,
  useDisputedAssessments,
  useReviewDisputedAssessment,
  useStartDailyResearch,
  type DailyResearchKind,
} from './queries';

type Tab = DailyResearchKind;
type JsonObject = Record<string, any>;

function QuantStockRefreshStatus() {
  const refresh = useMarketRefresh('stock');
  return <MarketRefreshStatus refresh={refresh} resources={[Resource.CN_STOCK_DAILY]} />;
}

const activeStatuses = new Set(['PENDING', 'QUEUED', 'RUNNING', 'RETRYING', 'CANCEL_REQUESTED']);
const directionLabel: Record<string, string> = {
  bullish: '偏利多', bearish: '偏利空', mixed: '多空分化', neutral: '中性',
  insufficient: '证据不足', unknown: '未知',
};
const slotLabels: Record<string, string> = {
  news_0900: '09:00 新闻分析',
  news_2100: '21:00 新闻分析',
  quant_2100: '21:00 量化策略扫描',
  news_incremental: '新闻增量分析',
  quant_news_refresh: '新闻补齐后候选重排',
  manual_news: '手动新闻分析',
  manual_quant: '手动量化策略扫描',
};

function formatTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
}

function safeExternalUrl(value?: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function taskStatusLabel(run: JsonObject) {
  if (run.report_status) return `${run.report_status} · ${run.status}`;
  const labels: Record<string, string> = {
    PENDING: '等待运行', QUEUED: '排队中', RUNNING: '运行中', RETRYING: '等待重试',
    SUCCEEDED: '已完成', FAILED: '失败', CANCELLED: '已取消', CANCEL_REQUESTED: '正在取消',
  };
  return labels[run.status] ?? run.status;
}

function eventTargets(event: JsonObject): string[] {
  return (event.targets ?? []).flatMap((target: JsonObject) =>
    (target.horizons ?? []).map((horizon: JsonObject) =>
      `${target.target ?? target.scope} ${horizon.trading_days}日${directionLabel[horizon.direction] ?? horizon.direction}`,
    ),
  );
}

export default function DailyResearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get('tab') === 'quant' ? 'quant' : 'news';
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const newsRunsQuery = useDailyResearchRuns('news');
  const quantRunsQuery = useDailyResearchRuns('quant');
  const runsQuery = tab === 'news' ? newsRunsQuery : quantRunsQuery;
  const runs = runsQuery.data ?? [];
  const latest = runs[0];
  const selected = runs.find((run) => run.task_id === selectedId) ?? latest;
  const detailQuery = useDailyResearchRun(selected?.task_id ?? null);
  const startRun = useStartDailyResearch();
  const newsHasActive = (newsRunsQuery.data ?? []).some((run) => activeStatuses.has(run.status));
  const quantHasActive = (quantRunsQuery.data ?? []).some((run) => activeStatuses.has(run.status));

  useEffect(() => {
    if (latest && (!selectedId || !runs.some((run) => run.task_id === selectedId))) {
      setSelectedId(latest.task_id);
    }
  }, [latest?.task_id, runs, selectedId]);

  const report = detailQuery.data?.report as JsonObject | null | undefined;
  const active = selected ? activeStatuses.has(selected.status) : false;

  function switchTab(next: Tab) {
    setSelectedId(null);
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      if (next === 'news') params.delete('tab');
      else params.set('tab', next);
      return params;
    }, { replace: true });
  }

  return (
    <main className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">每日研究</h1>
          <p className="mt-1 text-sm text-[var(--color-fg-muted)]">
            新到新闻默认每 30 分钟自动研判，并在每天 09:00、21:00 生成整点版本；全部已发布量化策略每天 21:00 扫描。这里也可以分别手动运行。
          </p>
        </div>
        <Link className="text-sm text-[var(--color-accent-bright)] underline" to="/event-study">
          浏览事件预测批次
        </Link>
      </header>

      <SectionTabs
        label="每日研究类型"
        value={tab}
        onChange={switchTab}
        items={[{ value: 'news', label: '新闻与市场展望' }, { value: 'quant', label: '量化策略扫描' }]}
      />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>新闻事件研究</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm text-[var(--color-fg-muted)]">采集截至时点的新闻，双 Agent 判新与打标，并输出未来 1、5、20 个交易日的事件展望。</p>
            <p className="text-xs text-[var(--color-fg-muted)]">最近批次：{formatTime(newsRunsQuery.data?.[0]?.created_at)}</p>
            <Button type="button" disabled={startRun.isPending || newsHasActive} onClick={() => startRun.mutate('news')}>
              {newsHasActive ? '新闻分析运行中' : startRun.isPending && startRun.variables === 'news' ? '正在提交…' : '立即分析新闻事件'}
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>量化策略扫描</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm text-[var(--color-fg-muted)]">收盘后运行各策略当前已发布版本，按策略信号和近 90 日有效事件证据排序，不自动下单。</p>
            {tab === 'quant' && <QuantStockRefreshStatus />}
            <p className="text-xs text-[var(--color-fg-muted)]">最近批次：{formatTime(quantRunsQuery.data?.[0]?.created_at)}</p>
            <Button type="button" disabled={startRun.isPending || quantHasActive} onClick={() => startRun.mutate('quant')}>
              {quantHasActive ? '量化扫描运行中' : startRun.isPending && startRun.variables === 'quant' ? '正在提交…' : '运行全部量化策略'}
            </Button>
          </CardContent>
        </Card>
      </div>

      {startRun.isError && <ErrorState error={startRun.error} />}
      {runsQuery.isError && <ErrorState error={runsQuery.error} onRetry={() => void runsQuery.refetch()} />}
      {runsQuery.isPending && <LoadingState label="读取每日研究批次…" />}
      {tab === 'news' && <DisputedAssessmentQueue />}

      {!runsQuery.isPending && (
        <section className="grid gap-4 lg:grid-cols-[minmax(220px,280px)_1fr]">
          <Card>
            <CardHeader><CardTitle>批次历史</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-2">
              {runs.length === 0 && <p className="text-sm text-[var(--color-fg-muted)]">还没有{tab === 'news' ? '新闻' : '量化'}批次。</p>}
              {runs.map((run) => (
                <button
                  key={run.task_id}
                  type="button"
                  onClick={() => setSelectedId(run.task_id)}
                  aria-pressed={run.task_id === selected?.task_id}
                  className="rounded-lg border p-3 text-left text-sm aria-pressed:border-[var(--color-accent-bright)]"
                  style={{ borderColor: 'var(--color-border)' }}
                >
                  <span className="block font-medium">{slotLabels[run.slot ?? ''] ?? (run.trigger === 'manual' ? '手动运行' : '定时运行')}</span>
                  <span className="mt-1 block text-xs text-[var(--color-fg-muted)]">{formatTime(run.created_at)}</span>
                  <span className="mt-1 block text-xs">{taskStatusLabel(run)}</span>
                </button>
              ))}
              {runsQuery.data && runsQuery.data.length >= 30 && <p className="text-xs text-[var(--color-fg-muted)]">当前显示最近 30 个批次。</p>}
            </CardContent>
          </Card>

          <div className="flex min-w-0 flex-col gap-4">
            {selected && (
              <Card>
                <CardHeader>
                  <CardTitle>{selected.kind === 'news' ? '新闻研究批次' : '量化策略批次'} · {taskStatusLabel(selected)}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-2 text-sm">
                  <p>创建于 {formatTime(selected.created_at)} · 截止 {formatTime(selected.news_cutoff_at || selected.scheduled_at)}</p>
                  {selected.error_summary && <p className="text-red-400">{selected.error_code}: {selected.error_summary}</p>}
                  {active && <p className="text-[var(--color-fg-muted)]">任务正在执行；页面会自动刷新状态。</p>}
                  {detailQuery.isPending && <LoadingState label="读取批次结果…" />}
                  {detailQuery.isError && <ErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />}
                </CardContent>
              </Card>
            )}
            {!selected && <Card><CardContent className="py-8 text-sm text-[var(--color-fg-muted)]">等待每日 09:00 / 21:00 自动批次，或手动开始一次研究。</CardContent></Card>}
            {report && tab === 'news' && <NewsReport report={report} taskId={selected?.task_id ?? ''} />}
            {report && tab === 'quant' && <QuantReport report={report} />}
          </div>
        </section>
      )}
    </main>
  );
}

function NewsReport({ report, taskId }: { report: JsonObject; taskId: string }) {
  const horizons = report.market_outlook?.horizons ?? {};
  const entries = Object.entries(horizons) as [string, JsonObject][];
  const marketAgents = report.market_outlook?.market_agents ?? {};
  const agentReports: [string, string | null][] = [
    ['中国市场事件分析', marketAgents.reports?.cn_news],
    ['中国市场技术分析', marketAgents.reports?.cn_tech],
    ['板块信息与资金分析', marketAgents.reports?.sector_news],
    ['板块技术确认', marketAgents.reports?.sector_tech],
    ['板块轮动判断', marketAgents.reports?.sector_rotation],
  ];
  const events: JsonObject[] = report.event_forecast ?? [];
  const newsResults: JsonObject[] = report.news_results ?? [];
  const eventCoverage = report.event_coverage ?? {};
  const unresolved = newsResults.filter((row) => ['disputed', 'failed'].includes(row.status));
  return (
    <>
      <Card>
        <CardHeader><CardTitle>市场事件展望</CardTitle></CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          {eventCoverage.complete === false && (
            <p className="text-sm text-amber-400 md:col-span-3">
              事件覆盖不完整：已结构化 {eventCoverage.included_count ?? 0}/{eventCoverage.assessed_event_count ?? 0} 条；
              另有 {eventCoverage.legacy_unassessed_event_count ?? 0} 条存量事件没有可审计的期限标签，未纳入权重。
            </p>
          )}
          {entries.map(([key, value]) => (
            <div key={key} className="rounded-lg border p-3" style={{ borderColor: 'var(--color-border)' }}>
              <p className="text-xs text-[var(--color-fg-muted)]">{key} 个交易日 · {value.date_range?.start ?? '日期待定'} 至 {value.date_range?.end ?? '日期待定'}</p>
              <p className="mt-2 text-lg font-semibold">{directionLabel[value.direction] ?? value.direction}</p>
              <p className="mt-1 text-xs text-[var(--color-fg-muted)]">证据置信等级 {value.confidence ?? 'low'} · 利多权重 {value.bullish_weight ?? 0} / 利空权重 {value.bearish_weight ?? 0}</p>
              {(value.invalidations ?? []).map((item: string) => <p key={item} className="mt-1 text-xs text-[var(--color-fg-muted)]">失效条件：{item}</p>)}
              {(value.drivers ?? []).slice(0, 3).map((driver: JsonObject) => <p key={`${driver.assessment_id}-${driver.title}`} className="mt-2 text-xs">· {driver.title}</p>)}
            </div>
          ))}
          {entries.length === 0 && <p className="text-sm text-[var(--color-fg-muted)]">本批次没有足够的市场方向证据。</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>市场与板块 Agent 研判</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-[var(--color-fg-muted)]">
            行情日期 {marketAgents.market_as_of_trade_date ?? report.market_as_of_trade_date ?? '—'} · 风险门控 {marketAgents.risk_gate ?? report.market_outlook?.risk_gate ?? '—'} · 状态 {marketAgents.status ?? '不可用'}
          </p>
          {marketAgents.error && <p className="text-sm text-amber-400">{marketAgents.error}</p>}
          {agentReports.filter(([, content]) => Boolean(content)).map(([title, content]) => (
            <details key={title} className="rounded-lg border p-3" style={{ borderColor: 'var(--color-border)' }}>
              <summary className="cursor-pointer text-sm font-medium">{title}</summary>
              <MarkdownView content={content ?? ''} className="mt-3" imagePolicy="text" />
            </details>
          ))}
          {agentReports.every(([, content]) => !content) && !marketAgents.error && (
            <p className="text-sm text-[var(--color-fg-muted)]">本批次没有可展示的市场/板块 Agent 报告。</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>新闻处理与复核</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          <p>本批处理 {report.news_count ?? newsResults.length} 条；待处理 {report.pending_news_count ?? 0} 条；来源状态 {report.source_status ?? '未知'}；截止后首次发现但已发布的新闻 {report.late_discovered_news_count ?? 0} 条。</p>
          {unresolved.length === 0 && <p className="text-[var(--color-fg-muted)]">本批没有判定争议或模型失败的新闻。</p>}
          {unresolved.slice(0, 50).map((row) => {
            const sourceUrl = safeExternalUrl(row.source_url);
            return (
              <article key={row.news_id} className="flex flex-wrap items-start justify-between gap-2 border-t pt-3" style={{ borderColor: 'var(--color-border)' }}>
                <div>
                  <p className="font-medium">{row.title ?? row.news_id}</p>
                  <p className="mt-1 text-xs text-[var(--color-fg-muted)]">{row.source ?? '未知来源'} · {row.status === 'disputed' ? '需要人工复核' : '模型处理失败'} · {row.unresolved_reasons?.join('；') || '暂无细节'}</p>
                </div>
                {sourceUrl && <a className="text-xs text-[var(--color-accent-bright)] underline" href={sourceUrl} target="_blank" rel="noreferrer">查看原文</a>}
              </article>
            );
          })}
          {unresolved.length > 50 && <p className="text-xs text-[var(--color-fg-muted)]">还有 {unresolved.length - 50} 条未在此展开。</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>本批次已知事件 · {events.length}</CardTitle></CardHeader>
        <CardContent className="divide-y" style={{ borderColor: 'var(--color-border)' }}>
          {events.slice(0, 100).map((event) => (
            <article key={event.assessment_id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <h3 className="font-medium">{event.title}</h3>
                <p className="mt-1 text-xs text-[var(--color-fg-muted)]">{event.stage ?? '阶段未知'} · {formatTime(event.available_at)}</p>
                <p className="mt-2 text-sm">{eventTargets(event).join('；') || '暂无可验证的方向判断'}</p>
              </div>
              <Link className="text-sm text-[var(--color-accent-bright)] underline" to={`/event-study?run_id=${taskId}&event_id=${event.event_id}`}>
                查看证据与兑现情况
              </Link>
            </article>
          ))}
          {events.length === 0 && <p className="py-4 text-sm text-[var(--color-fg-muted)]">本批次尚无已确认或待复核事件。</p>}
          {events.length > 100 && <p className="pt-3 text-xs text-[var(--color-fg-muted)]">总计 {events.length} 条，事件研究页可继续查看本批次。</p>}
        </CardContent>
      </Card>
    </>
  );
}

function DisputedAssessmentQueue() {
  const query = useDisputedAssessments();
  const review = useReviewDisputedAssessment();
  const [labelsText, setLabelsText] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [localError, setLocalError] = useState<string | null>(null);
  const items = query.data ?? [];

  useEffect(() => {
    setLabelsText((current) => {
      const next = { ...current };
      for (const row of items) {
        if (next[row.assessment_id] === undefined) {
          next[row.assessment_id] = JSON.stringify(row.labels ?? {}, null, 2);
        }
      }
      return next;
    });
    setNotes((current) => {
      const next = { ...current };
      for (const row of items) next[row.assessment_id] ??= '人工复核确认';
      return next;
    });
  }, [query.data]);

  function submit(row: NonNullable<typeof items>[number], status: 'accepted' | 'rejected' | 'retracted') {
    let labels: JsonObject | undefined;
    if (status === 'accepted') {
      try {
        labels = JSON.parse(labelsText[row.assessment_id] ?? '{}') as JsonObject;
      } catch {
        setLocalError(`“${row.title}”的标签不是有效 JSON。`);
        return;
      }
    }
    setLocalError(null);
    review.mutate({
      assessmentId: row.assessment_id,
      payload: {
        expected_revision: row.revision,
        review_status: status as AssessmentReviewRequest['review_status'],
        labels,
        review_note: notes[row.assessment_id] ?? '人工复核',
      },
    });
  }

  return (
    <Card>
      <CardHeader><CardTitle>待人工复核 · {items.length}</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p className="text-xs text-[var(--color-fg-muted)]">复核会追加一个不可变判断版本，并自动启动新闻展望重算；历史版本仍可追溯。</p>
        {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
        {query.isPending && <LoadingState label="读取待复核事件…" />}
        {localError && <p role="alert" className="text-red-400">{localError}</p>}
        {review.isError && <ErrorState error={review.error} />}
        {review.isSuccess && <p className="text-emerald-400">复核已保存，新闻展望任务 {review.data.task_status}。</p>}
        {!query.isPending && !query.isError && items.length === 0 && (
          <p className="text-[var(--color-fg-muted)]">当前没有待复核的事件判断。</p>
        )}
        {items.map((row) => {
          const sourceUrl = safeExternalUrl(row.source_url);
          const evidence = row.evidence ?? [];
          return (
            <article key={row.assessment_id} className="flex flex-col gap-3 border-t pt-4" style={{ borderColor: 'var(--color-border)' }}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="font-medium">{row.title}</h3>
                  <p className="mt-1 text-xs text-[var(--color-fg-muted)]">
                    {row.source_label ?? row.source} · 事件 {row.event_id} · 判断版本 {row.revision} · {formatTime(row.available_at)}
                  </p>
                </div>
                {sourceUrl && <a className="text-xs text-[var(--color-accent-bright)] underline" href={sourceUrl} target="_blank" rel="noreferrer">打开原文</a>}
              </div>
              <details>
                <summary className="cursor-pointer text-xs text-[var(--color-accent-bright)]">查看原文与证据（{evidence.length} 条）</summary>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--color-bg-subtle)] p-3 text-xs">{row.raw_content}</pre>
                {evidence.map((item) => <blockquote key={item.evidence_id} className="mt-2 border-l-2 pl-3 text-xs text-[var(--color-fg-muted)]">{item.quote}</blockquote>)}
              </details>
              <label className="flex flex-col gap-1 text-xs">
                判断标签 JSON（可修改后确认）
                <textarea
                  aria-label={`判断标签 JSON：${row.title}`}
                  rows={14}
                  spellCheck={false}
                  className="rounded-lg border bg-[var(--color-bg)] p-3 font-mono text-xs"
                  style={{ borderColor: 'var(--color-border)' }}
                  value={labelsText[row.assessment_id] ?? JSON.stringify(row.labels ?? {}, null, 2)}
                  onChange={(event) => setLabelsText((current) => ({ ...current, [row.assessment_id]: event.target.value }))}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                复核说明
                <input
                  aria-label={`复核说明：${row.title}`}
                  className="rounded-lg border bg-[var(--color-bg)] px-3 py-2 text-sm"
                  style={{ borderColor: 'var(--color-border)' }}
                  value={notes[row.assessment_id] ?? '人工复核确认'}
                  onChange={(event) => setNotes((current) => ({ ...current, [row.assessment_id]: event.target.value }))}
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={review.isPending} onClick={() => submit(row, 'accepted')}>保存标签并确认</Button>
                <Button type="button" variant="outline" disabled={review.isPending} onClick={() => submit(row, 'rejected')}>驳回此判断</Button>
                <Button type="button" variant="outline" disabled={review.isPending} onClick={() => submit(row, 'retracted')}>撤销此事实</Button>
              </div>
            </article>
          );
        })}
      </CardContent>
    </Card>
  );
}

function QuantReport({ report }: { report: JsonObject }) {
  const candidates: JsonObject[] = report.candidates ?? [];
  const strategies: JsonObject[] = report.strategies ?? [];
  return (
    <>
      <Card>
        <CardHeader><CardTitle>扫描概况</CardTitle></CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-3">
          <p>目标交易日：{report.requested_trade_date ?? report.target_trade_date ?? '—'}</p>
          <p>实际行情日：{report.market_as_of_trade_date ?? '—'}{report.used_latest_complete_market_data ? '（复用最近完整数据）' : ''}</p>
          <p>市场状态：{report.market_session_status === 'CLOSED' ? '休市' : '交易日'}</p>
          <p>已运行策略：{report.strategy_count ?? strategies.length}</p>
          <p>候选股票：{report.candidate_total ?? candidates.length}</p>
          <p>重点深研：{report.deep_research_count ?? 0} 成功 · {report.deep_research_failed_count ?? 0} 失败</p>
          {report.strategy_scan_reused && <p className="text-[var(--color-fg-muted)] sm:col-span-3">新闻补齐后重新计算事件权重并排序，复用原量化批次的策略扫描结果。</p>}
          {report.warnings?.map((warning: string) => <p key={warning} className="text-amber-400 sm:col-span-3">{warning}</p>)}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>同截止点市场风险背景</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p>风险门控：{report.risk_gate ?? '不可用'} · 新闻批次：{report.news_research_dependency?.status ?? '无依赖信息'} · 事件证据完整：{report.event_evidence_complete ? '是' : '否'}</p>
          <p className="text-xs text-[var(--color-fg-muted)]">行情日期 {report.market_outlook?.market_agents?.market_as_of_trade_date ?? '—'} · 新闻截止 {formatTime(report.event_news_cutoff_at)} · 判断版本时点 {formatTime(report.event_as_of)}</p>
          {report.market_outlook?.risk_gate_reason && <p className="text-xs text-[var(--color-fg-muted)]">门控说明：{report.market_outlook.risk_gate_reason}</p>}
          {report.market_outlook?.market_agents?.reports?.cn_tech && (
            <details className="rounded-lg border p-3" style={{ borderColor: 'var(--color-border)' }}>
              <summary className="cursor-pointer font-medium">查看同一时点的中国技术分析</summary>
              <MarkdownView content={report.market_outlook.market_agents.reports.cn_tech} className="mt-3" imagePolicy="text" />
            </details>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>筛选股票与事件权重</CardTitle></CardHeader>
        <CardContent className="overflow-x-auto">
          {report.position_planning?.reason && <p className="mb-3 text-sm text-[var(--color-fg-muted)]">{report.position_planning.reason}</p>}
          {candidates.length === 0 ? <p className="text-sm text-[var(--color-fg-muted)]">本批次没有符合条件的候选股票。</p> : (
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead><tr className="border-b text-xs text-[var(--color-fg-muted)]"><th className="py-2">排名</th><th>股票</th><th>策略信号</th><th>量化分</th><th>事件调整</th><th>推荐排序分</th><th>个股深研</th></tr></thead>
              <tbody>
                {candidates.slice(0, 100).map((candidate, index) => (
                  <tr key={candidate.ticker} className="border-b" style={{ borderColor: 'var(--color-border)' }}>
                    <td className="py-3">{index + 1}</td>
                    <td className="font-medium">{candidate.ticker}</td>
                    <td>{(candidate.strategies ?? []).map((row: JsonObject) => row.strategy_name).join('、')}</td>
                    <td>{candidate.quant_score}</td>
                    <td className={(candidate.event_score ?? 0) < 0 ? 'text-red-400' : 'text-emerald-400'}>
                      {candidate.event_score > 0 ? '+' : ''}{candidate.event_score}
                      <span className="ml-1 block text-xs text-[var(--color-fg-muted)]">
                        {candidate.event_evidence_status === 'available'
                          ? (candidate.event_positive_score === 0 && candidate.event_negative_score === 0
                            ? '存在中性/多空分化证据，净权重为 0'
                            : `利好 +${candidate.event_positive_score ?? 0} / 利空 ${candidate.event_negative_score ?? 0}`)
                          : '无合格个股事件证据'}
                      </span>
                      {(candidate.event_drivers ?? []).slice(0, 3).map((driver: JsonObject) => (
                        <span key={`${driver.assessment_id}-${driver.fact_key}`} className="mt-1 block text-xs text-[var(--color-fg-muted)]">
                          {driver.direction === 'bearish' ? '利空' : driver.direction === 'mixed' ? '多空分化' : driver.direction === 'neutral' ? '中性' : '利好'} {driver.title}
                        </span>
                      ))}
                    </td>
                    <td className="font-semibold">{candidate.total_score}</td>
                    <td>
                      {candidate.deep_research?.status === 'completed' ? (
                        <details className="min-w-48">
                          <summary className="cursor-pointer text-[var(--color-accent-bright)]">查看深研</summary>
                          <div className="mt-2 flex max-w-2xl flex-col gap-2 text-xs">
                            <p>行情日 {candidate.deep_research.market_as_of_trade_date ?? '—'} · 风险门控 {candidate.deep_research.risk_gate ?? '—'} · 匹配事件 {candidate.deep_research.event_count ?? 0}</p>
                            {candidate.deep_research.decision_text && <p className="whitespace-pre-wrap">{candidate.deep_research.decision_text}</p>}
                            {(candidate.deep_research.events ?? []).slice(0, 5).map((event: JsonObject) => (
                              <p key={`${event.assessment_id}-${event.title}`} className="text-[var(--color-fg-muted)]">· {event.title} · {event.stage ?? '阶段未知'}</p>
                            ))}
                            {Object.entries(candidate.deep_research.reports ?? {}).filter(([, content]) => Boolean(content)).map(([name, content]) => (
                              <details key={name} className="rounded border p-2" style={{ borderColor: 'var(--color-border)' }}>
                                <summary className="cursor-pointer font-medium">{name}</summary>
                                <MarkdownView content={String(content)} className="mt-2" imagePolicy="text" />
                              </details>
                            ))}
                          </div>
                        </details>
                      ) : candidate.deep_research?.status === 'failed' ? (
                        <span className="text-amber-400">深研失败</span>
                      ) : (
                        <span className="text-xs text-[var(--color-fg-muted)]">未进入前 10 深研</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
