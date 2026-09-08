import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Checkbox } from '../../../../shared/ui/checkbox';
import { ConfirmDialog } from '../../../../shared/ui/ConfirmDialog';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import { toImpactRowVM, type ImpactRowVM } from './mappers/toImpactRowVM';
import { useConfirmImpactsMutation, useImpactDraftsQuery } from './queries';

const IMPACT_ROW_GRID =
  'grid grid-cols-[2.5rem_4rem_minmax(0,1.5fr)_7rem_7rem_6rem_5rem_5rem_5rem_5rem_minmax(0,1fr)] items-center gap-2';

// Tab2 影响结果确认：资产×窗口行勾选 → 按事件分组逐事件确认落表（仅勾选的写入 event_impacts）。
export function ImpactConfirmTab() {
  const query = useImpactDraftsQuery();
  const queryClient = useQueryClient();
  const confirmMutation = useConfirmImpactsMutation();

  const rows = useMemo(() => (query.data?.items ?? []).flatMap(toImpactRowVM), [query.data]);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // 数据刷新（确认落表后草稿消失）时重置勾选
  useEffect(() => {
    setChecked(new Set());
  }, [query.data]);

  if (query.isPending) return <LoadingState label="加载影响草稿…" />;
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }

  const toggle = (key: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // error 窗口不可勾选（服务端确认时静默跳过，勾选会误导落表数）；全选只含可勾选行
  const selectable = rows.filter((r) => !r.error);
  const toggleAll = () =>
    setChecked((prev) =>
      prev.size === selectable.length && selectable.length > 0 ? new Set() : new Set(selectable.map((r) => r.key)),
    );

  async function runConfirm() {
    const selected = rows.filter((r) => checked.has(r.key) && !r.error);
    if (selected.length === 0) {
      setMsg('请至少勾选一行');
      setConfirmOpen(false);
      return;
    }
    setConfirmOpen(false);
    setConfirming(true);
    // 勾选行按 event_id 分组聚合 tickers（confirm 以事件为粒度）
    const byEvent = new Map<number, Set<string>>();
    for (const r of selected) {
      const tickers = byEvent.get(r.eventId) ?? new Set<string>();
      tickers.add(r.ticker);
      byEvent.set(r.eventId, tickers);
    }
    let inserted = 0;
    const failures: string[] = [];
    for (const [eventId, tickers] of byEvent) {
      try {
        const data = await confirmMutation.mutateAsync({
          eventId,
          request: { tickers: [...tickers], operator: 'admin' },
        });
        inserted += data.inserted;
      } catch (err) {
        failures.push(`事件 ${eventId}：${toApiError(err).message}`);
      }
    }
    setConfirming(false);
    setMsg(`已确认落表 ${inserted} 条${failures.length > 0 ? `；${failures.length} 个事件失败：${failures.join('；')}` : ''}`);
    void queryClient.invalidateQueries({ queryKey: queryKeys.eventStudyReview.all });
  }

  return (
    <section className="flex flex-col gap-4">
      {rows.length === 0 ? (
        <EmptyState
          title="暂无影响结果草稿"
          description="事件审核通过后自动计算影响结果草稿（存 Redis，7 天过期）；过期或缺失时每日批处理会自动兜底重算，批量审核结果中的「影响计算失败」徽章也可触发补算"
        />
      ) : (
        <>
          <div className="overflow-x-auto">
            <div className={`${IMPACT_ROW_GRID} pb-1 text-xs`} style={{ color: 'var(--color-fg-muted)' }}>
              <span>
                <Checkbox
                  aria-label="全选"
                  checked={checked.size === selectable.length && selectable.length > 0}
                  onChange={toggleAll}
                />
              </span>
              <span>#</span>
              <span>事件标题</span>
              <span>t0</span>
              <span>资产</span>
              <span>窗口</span>
              <span>CAR%</span>
              <span>t值</span>
              <span>方向</span>
              <span>污染</span>
              <span>备注</span>
            </div>
            {rows.map((row) => (
              <ImpactRow
                key={row.key}
                row={row}
                checked={checked.has(row.key)}
                onToggle={() => toggle(row.key)}
              />
            ))}
          </div>
          <div className="flex items-center gap-3">
            <Button disabled={confirming} onClick={() => setConfirmOpen(true)}>
              {confirming ? '确认中…' : '🚀 确认落表'}
            </Button>
            {msg && (
              <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {msg}
              </p>
            )}
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="确认影响结果落表"
        description={`将勾选的 ${checked.size} 行（按事件分组，error 窗口自动跳过）写入 event_impacts 正式记录，对应影响草稿将被删除`}
        confirmLabel="确认落表"
        pending={confirming}
        onConfirm={() => void runConfirm()}
        onCancel={() => setConfirmOpen(false)}
      />
    </section>
  );
}

function ImpactRow({ row, checked, onToggle }: { row: ImpactRowVM; checked: boolean; onToggle: () => void }) {
  const hasError = row.error !== null;
  return (
    <div className={`${IMPACT_ROW_GRID} border-b py-2`} style={{ borderColor: 'var(--color-border)' }}>
      <span>
        <Checkbox
          aria-label={`勾选 ${row.key}`}
          checked={checked}
          disabled={hasError}
          title={hasError ? 'error 窗口，确认时自动跳过' : undefined}
          onChange={onToggle}
        />
      </span>
      <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
        {row.eventId}
      </span>
      <span className="truncate text-sm" title={row.title}>
        {row.title}
      </span>
      <span className="text-xs">{row.t0 ?? '—'}</span>
      <span className="text-xs">{row.ticker}</span>
      <span className="text-xs">{row.windowType}</span>
      <span className="text-sm">{row.carPct || '—'}</span>
      <span className="text-xs">{row.tStat || '—'}</span>
      <span className="text-xs">{row.directionLabel}</span>
      <span>{row.contaminated ? <Badge variant="warning">⚠️</Badge> : ''}</span>
      <span className="truncate text-xs" title={row.error ?? ''} style={{ color: 'var(--color-fg-muted)' }}>
        {row.error ?? ''}
      </span>
    </div>
  );
}
