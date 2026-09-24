import { useEffect, useState } from 'react';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Button } from '../../../shared/ui/button';
import { ConfirmDialog } from '../../../shared/ui/ConfirmDialog';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import type { PortfolioPositionDTO } from '../../../api/generated';
import { usePositionsQuery, useRemovePositionMutation, useUpsertPositionMutation } from '../queries';

// 持仓列表：按 market ASC, symbol ASC（服务端顺序，不本地重排）；upsert 表单
// 数量 > 0、平均成本 >= 0（前端先行 + 服务端兜底 INVALID_POSITION 字段错误）；
// 删除确认；409 冲突后重新拉取父/子 Query。
export function PositionList({ portfolioId, onDirtyChange }: { portfolioId: string | null; onDirtyChange?: (dirty: boolean) => void }) {
  const positionsQuery = usePositionsQuery(portfolioId);
  const upsertMutation = useUpsertPositionMutation(portfolioId ?? '');
  const removeMutation = useRemovePositionMutation(portfolioId ?? '');

  const [market, setMarket] = useState('CN');
  const [symbol, setSymbol] = useState('');
  const [quantity, setQuantity] = useState('');
  const [averageCost, setAverageCost] = useState('');
  const [activeStop, setActiveStop] = useState('');
  const [removeTarget, setRemoveTarget] = useState<PortfolioPositionDTO | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [baseline, setBaseline] = useState<{ revision: number; original: PortfolioPositionDTO | null; ids: string[] } | null>(null);
  const [conflict, setConflict] = useState(false);
  useEffect(() => { onDirtyChange?.(baseline !== null); }, [baseline, onDirtyChange]);

  function beginDraft() {
    if (!baseline && positionsQuery.data) setBaseline({ revision: positionsQuery.data.revision, original: null, ids: positionsQuery.data.items.map(item => `${item.market}:${item.symbol}`) });
  }
  function resetDraft() {
    setSymbol(''); setQuantity(''); setAverageCost(''); setActiveStop(''); setBaseline(null); setConflict(false); setFormError(null); upsertMutation.reset();
  }
  function editPosition(position: PortfolioPositionDTO) {
    setMarket(position.market); setSymbol(position.symbol); setQuantity(String(position.quantity)); setAverageCost(String(position.average_cost)); setActiveStop(position.active_stop_price?.toString() ?? '');
    setBaseline({ revision: positionsQuery.data!.revision, original: position, ids: positionsQuery.data!.items.map(item => `${item.market}:${item.symbol}`) }); setConflict(false); setFormError(null); upsertMutation.reset();
  }
  if (portfolioId === null) {
    return <EmptyState title="先选择一个组合，再查看或添加持仓" />;
  }

  if (positionsQuery.isPending) return <LoadingState label="持仓列表加载中…" />;
  if (positionsQuery.isError && !positionsQuery.data) {
    const error = toApiError(positionsQuery.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void positionsQuery.refetch() : undefined} />;
  }

  const state = positionsQuery.data;
  const positions = state?.items ?? [];
  const revision = state?.revision ?? 0;

  const stale = conflict || (baseline !== null && baseline.revision !== revision);
  const duplicate = !baseline?.original && positions.find(item => item.market === market && item.symbol === symbol.trim());

  function submitUpsert() {
    setFormError(null);
    if (!baseline || stale) { setFormError('持仓已更新，请显式重新加载后编辑；草稿已保留。'); return; }
    if (duplicate || (!baseline.original && baseline.ids.includes(`${market}:${symbol.trim()}`))) { setFormError('此标的已存在，请点击编辑现有持仓。'); return; }
    const parsedQuantity = Number(quantity);
    const parsedCost = Number(averageCost);
    const parsedStop = activeStop.trim() ? Number(activeStop) : null;
    if (!symbol.trim()) {
      setFormError('请输入标的代码');
      return;
    }
    if (!Number.isFinite(parsedQuantity) || parsedQuantity <= 0) {
      setFormError('数量必须大于 0');
      return;
    }
    if (!averageCost.trim() || !Number.isFinite(parsedCost) || parsedCost < 0) {
      setFormError('平均成本不能小于 0');
      return;
    }
    if (parsedStop !== null && (!Number.isFinite(parsedStop) || parsedStop <= 0)) {
      setFormError('有效止损必须大于 0，或留空');
      return;
    }
    upsertMutation.mutate(
      {
        market,
        symbol: symbol.trim(),
        quantity: parsedQuantity,
        averageCost: parsedCost,
        activeStopPrice: parsedStop,
        expectedPortfolioRevision: baseline.revision,
      },
      {
        onSuccess: resetDraft,
        onError: (error) => {
          const apiError = toApiError(error);
          if (apiError.status === 409) setConflict(true);
          setFormError(apiError.code === 'INVALID_POSITION' ? '持仓数据非法：请检查数量与平均成本' : apiError.message);
        },
      },
    );
  }

  function confirmRemove() {
    if (!removeTarget) return;
    removeMutation.mutate(
      { market: removeTarget.market, symbol: removeTarget.symbol, expectedPortfolioRevision: revision },
      {
        onSuccess: () => setRemoveTarget(null),
        onError: () => setRemoveTarget(null),
      },
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {positionsQuery.isError && <p role="status" className="text-xs text-amber-500">更新失败，当前显示上次持仓。<button type="button" className="ml-2 underline" onClick={() => void positionsQuery.refetch()}>重新加载</button></p>}
      <div className="rounded-xl bg-[var(--color-bg)] p-4" onChangeCapture={beginDraft}>
        <p className="mb-3 text-sm font-medium">{baseline?.original ? `编辑持仓 · ${symbol}` : '新增持仓'}</p>
        <p className="mb-3 text-xs text-[var(--color-fg-muted)]">数量按实际股数/份数填写，不按手数换算。</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pos-market">市场</Label>
          <select
            id="pos-market"
            disabled={!!baseline?.original}
            className="h-9 rounded-md border bg-transparent px-3 text-sm"
            style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
            value={market}
            onChange={(event) => setMarket(event.target.value)}
          >
            <option value="US">US</option>
            <option value="KR">KR</option>
            <option value="CN">CN</option>
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pos-symbol">标的代码</Label>
          <Input disabled={!!baseline?.original} id="pos-symbol" placeholder="如 000001.SZ" value={symbol} onChange={(event) => setSymbol(event.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pos-quantity">数量</Label>
          <Input
            id="pos-quantity"
            type="number"
            placeholder="> 0"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pos-cost">平均成本</Label>
          <Input
            id="pos-cost"
            type="number"
            placeholder=">= 0"
            value={averageCost}
            onChange={(event) => setAverageCost(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pos-stop">有效止损（可空）</Label>
          <Input id="pos-stop" type="number" placeholder="> 0" value={activeStop} onChange={(event) => setActiveStop(event.target.value)} />
        </div>
        </div>
        {duplicate && <p className="mt-3 text-xs text-amber-500">此标的已存在。<button type="button" className="ml-2 underline" onClick={() => editPosition(duplicate)}>放弃新增草稿并编辑现有持仓</button></p>}
        {stale && <p role="alert" className="mt-3 text-xs text-amber-500">持仓已被更新，草稿已保留。<button type="button" className="ml-2 underline" onClick={resetDraft}>放弃草稿并重新加载</button></p>}
        <div className="mt-4 flex gap-2">
        <Button size="sm" disabled={upsertMutation.isPending || stale || !!duplicate} onClick={submitUpsert}>
          {upsertMutation.isPending ? '保存中…' : baseline?.original ? '保存修改' : '添加持仓'}
        </Button>
        {baseline && <Button variant="ghost" size="sm" onClick={resetDraft}>取消编辑</Button>}
        </div>
      </div>

      {formError && <p className="text-xs text-red-400">{formError}</p>}

      {positions.length === 0 && <EmptyState title="组合暂无持仓" />}

      <ul className="flex flex-col gap-1">
        {positions.map((position) => (
          <li key={`${position.market}:${position.symbol}`}>
            <div
              className="flex items-center justify-between gap-2 rounded-md border p-2"
              style={{ borderColor: 'var(--color-border)' }}
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span style={{ color: 'var(--color-fg-muted)' }}>{position.market}</span>
                <span className="font-medium">{position.symbol}</span>
                <span>数量 {position.quantity}</span>
                <span>成本 {position.average_cost}</span>
                <span>止损 {position.active_stop_price ?? '未设置'}</span>
              </div>
              <div className="flex shrink-0 gap-1"><Button size="sm" variant="ghost" disabled={baseline !== null || upsertMutation.isPending} onClick={() => editPosition(position)}>编辑</Button>
              <Button size="sm" variant="ghost" onClick={() => setRemoveTarget(position)}>
                删除
              </Button></div>
            </div>
          </li>
        ))}
      </ul>

      <ConfirmDialog
        open={removeTarget !== null}
        title={`删除持仓「${removeTarget?.market}:${removeTarget?.symbol}」`}
        description="删除后该持仓记录将被移除"
        confirmLabel="移除"
        pending={removeMutation.isPending}
        onConfirm={confirmRemove}
        onCancel={() => setRemoveTarget(null)}
      />
    </div>
  );
}
