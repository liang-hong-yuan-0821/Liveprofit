import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { ConfirmDialog } from '../../../../shared/ui/ConfirmDialog';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import type { ReviewRowRequest, ReviewRowResult } from '../../../../api/generated';
import { EventDetailDialog } from './EventDetailDialog';
import { PendingEventRow, PENDING_ROW_GRID } from './PendingEventRow';
import { toPendingEventRowVM, type PendingEventRowVM } from './mappers/toPendingEventRowVM';
import { useBatchMutation, useComputeMutation, usePendingEventsQuery, usePrelabelMutation } from './queries';

const CHUNK_SIZE = 10;

// Tab1 待审核事件：批量可编辑表格 + AI 预填循环 + 分块提交。
// 分块与预填循环均为页面编排（不进 mutation），规避 45s 默认超时（batch/prelabel 用 300s）。
export function PendingEventsTab() {
  const query = usePendingEventsQuery();
  const queryClient = useQueryClient();
  const prelabelMutation = usePrelabelMutation();
  const batchMutation = useBatchMutation();
  const computeMutation = useComputeMutation();

  const [rows, setRows] = useState<Record<number, PendingEventRowVM>>({});
  const [detailDraftId, setDetailDraftId] = useState<number | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [prelabelMsg, setPrelabelMsg] = useState<string | null>(null);
  const [prelabeling, setPrelabeling] = useState(false);
  const [batchMsg, setBatchMsg] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<ReviewRowResult[]>([]);
  const [computeRetryMsg, setComputeRetryMsg] = useState<Record<number, string>>({});

  useEffect(() => {
    if (query.data) {
      setRows(Object.fromEntries(query.data.items.map((d) => [d.draft_id, toPendingEventRowVM(d)])));
    }
  }, [query.data]);

  if (query.isPending) return <LoadingState label="加载待审事件…" />;
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  const items = query.data?.items ?? [];

  const patchRow = (draftId: number, patch: Partial<PendingEventRowVM>) =>
    setRows((prev) => (prev[draftId] ? { ...prev, [draftId]: { ...prev[draftId], ...patch } } : prev));

  function rowToRequest(vm: PendingEventRowVM): ReviewRowRequest {
    return {
      draft_id: vm.draftId,
      action: vm.action as ReviewRowRequest['action'],
      event_type: vm.eventType || null,
      event_subtype: vm.eventSubtype || null,
      event_condition: vm.eventCondition || null,
      importance: vm.importance,
      expected_value: vm.expectedValue === '' ? null : Number(vm.expectedValue),
      actual_value: vm.actualValue === '' ? null : Number(vm.actualValue),
      previous_value: vm.previousValue === '' ? null : Number(vm.previousValue),
      operator: 'admin',
    };
  }

  async function runPrelabel() {
    setPrelabeling(true);
    setPrelabelMsg('AI 预填中…');
    let total = 0;
    try {
      for (;;) {
        const r = await prelabelMutation.mutateAsync({ limit: 50 });
        total += r.prelabeled;
        if (r.remaining === 0) {
          setPrelabelMsg(total > 0 ? `已预填 ${total} 条` : '没有需要预填的草稿');
          break;
        }
        if (r.prelabeled === 0) {
          // 护栏：LLM 不可用/全部预填失败时 remaining 恒 >0，必须停止防死循环
          setPrelabelMsg(`已预填 ${total} 条；LLM 不可用或全部预填失败，已停止`);
          break;
        }
        setPrelabelMsg(`已预填 ${total} 条，剩余 ${r.remaining} 条…`);
      }
    } catch (err) {
      setPrelabelMsg(`预填失败：${toApiError(err).message}`);
    } finally {
      setPrelabeling(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.eventStudyReview.all });
    }
  }

  async function runBatch() {
    const targets = Object.values(rows).filter((r) => r.action !== 'skip');
    if (targets.length === 0) {
      setBatchMsg('请至少将一行「操作」设为通过或忽略');
      setConfirmOpen(false);
      return;
    }
    setConfirmOpen(false);
    setSubmitting(true);
    setBatchMsg(null);
    setResults([]);
    setComputeRetryMsg({});
    const chunks: ReviewRowRequest[][] = [];
    for (let i = 0; i < targets.length; i += CHUNK_SIZE) {
      chunks.push(targets.slice(i, i + CHUNK_SIZE).map(rowToRequest));
    }
    const collected: ReviewRowResult[] = [];
    const failedChunks: string[] = [];
    try {
      for (const [idx, chunk] of chunks.entries()) {
        setBatchMsg(`已提交 ${collected.length}/${targets.length} 行（第 ${idx + 1}/${chunks.length} 块）…`);
        try {
          const data = await batchMutation.mutateAsync({ items: chunk });
          collected.push(...data.results);
        } catch (err) {
          failedChunks.push(`第 ${idx + 1} 块（${toApiError(err).message}）`);
        }
      }
      setResults(collected);
      // 失败块信息并入最终消息（不被覆盖），失败行仍在表格中可重新提交
      if (failedChunks.length > 0) {
        setBatchMsg(`已处理 ${collected.length}/${targets.length} 行；失败块未处理：${failedChunks.join('；')}`);
      } else {
        setBatchMsg(`已处理 ${collected.length}/${targets.length} 行`);
      }
    } finally {
      setSubmitting(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.eventStudyReview.all });
    }
  }

  async function retryCompute(eventId: number) {
    try {
      const data = await computeMutation.mutateAsync({ eventId, request: { operator: 'admin' } });
      setComputeRetryMsg((prev) => ({
        ...prev,
        [eventId]: data.status === 'ok' ? '已触发重算' : (data.message ?? '重算未成功'),
      }));
    } catch (err) {
      setComputeRetryMsg((prev) => ({ ...prev, [eventId]: `重算失败：${toApiError(err).message}` }));
    }
  }

  const orderedRows = Object.values(rows);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          disabled={prelabeling || items.length === 0}
          onClick={() => void runPrelabel()}
        >
          {prelabeling ? 'AI 预填中…' : '🤖 AI 预填全部待审事件'}
        </Button>
        {prelabelMsg && (
          <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {prelabelMsg}
          </p>
        )}
      </div>

      {items.length === 0 ? (
        <EmptyState title="暂无待审核事件" description="爬虫采集的事件草稿会出现在这里（草稿存 Redis，30 天过期）" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <div className={`${PENDING_ROW_GRID} pb-1 text-xs`} style={{ color: 'var(--color-fg-muted)' }}>
              <span>#</span>
              <span>时间</span>
              <span>来源</span>
              <span>标题</span>
              <span>事件类型</span>
              <span>事件子类型</span>
              <span>关键条件</span>
              <span>重要性</span>
              <span>预期</span>
              <span>实际</span>
              <span>前值</span>
              <span>操作</span>
            </div>
            {orderedRows.map((vm) => (
              <PendingEventRow
                key={vm.draftId}
                vm={vm}
                disabled={prelabeling}
                onChange={(patch) => patchRow(vm.draftId, patch)}
                onShowDetail={() => setDetailDraftId(vm.draftId)}
              />
            ))}
          </div>
          <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            预期/实际/前值默认取 AI 提取值；清空 = 该字段不落值。数值 0 是合法值。
          </p>
          <div className="flex items-center gap-3">
            <Button disabled={submitting || prelabeling} onClick={() => setConfirmOpen(true)}>
              {submitting ? '提交中…' : '🚀 批量提交'}
            </Button>
            {batchMsg && (
              <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {batchMsg}
              </p>
            )}
          </div>
          {results.length > 0 && (
            <ul className="flex flex-col gap-1">
              {results.map((r) => (
                <li key={r.draft_id} className="flex items-center gap-2 text-sm">
                  <span style={{ color: 'var(--color-fg-muted)' }}>#{r.draft_id}</span>
                  {!r.ok ? (
                    <>
                      <Badge variant="destructive">失败</Badge>
                      <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                        {r.error_code}：{r.error_message}
                      </span>
                    </>
                  ) : r.compute_status === 'skipped' ? (
                    <Badge variant="secondary">已忽略</Badge>
                  ) : r.compute_status === 'failed' ? (
                    <>
                      <Badge variant="warning">已通过·影响计算失败</Badge>
                      {r.event_id != null && (
                        <Button size="sm" variant="outline" onClick={() => void retryCompute(r.event_id!)}>
                          重试
                        </Button>
                      )}
                      {computeRetryMsg[r.event_id ?? 0] && (
                        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                          {computeRetryMsg[r.event_id ?? 0]}
                        </span>
                      )}
                    </>
                  ) : (
                    <Badge variant="success">已通过</Badge>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="批量提交审核"
        description="将按各行的「操作」提交（通过/忽略），跳过行不处理；提交后草稿将从待审队列移除并写入事件库"
        confirmLabel="提交"
        pending={submitting}
        onConfirm={() => void runBatch()}
        onCancel={() => setConfirmOpen(false)}
      />

      <EventDetailDialog
        open={detailDraftId !== null}
        vm={detailDraftId !== null ? rows[detailDraftId] ?? null : null}
        onClose={() => setDetailDraftId(null)}
      />
    </section>
  );
}
