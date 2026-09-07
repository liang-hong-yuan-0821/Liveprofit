import { useState } from 'react';
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
export function PositionList({ portfolioId }: { portfolioId: string | null }) {
  const positionsQuery = usePositionsQuery(portfolioId);
  const upsertMutation = useUpsertPositionMutation(portfolioId ?? '');
  const removeMutation = useRemovePositionMutation(portfolioId ?? '');

  const [market, setMarket] = useState('CN');
  const [symbol, setSymbol] = useState('');
  const [quantity, setQuantity] = useState('');
  const [averageCost, setAverageCost] = useState('');
  const [removeTarget, setRemoveTarget] = useState<PortfolioPositionDTO | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  if (portfolioId === null) {
    return <EmptyState title="选择左侧组合查看持仓" />;
  }

  if (positionsQuery.isPending) return <LoadingState label="持仓列表加载中…" />;
  if (positionsQuery.isError) {
    const error = toApiError(positionsQuery.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void positionsQuery.refetch() : undefined} />;
  }

  const state = positionsQuery.data;
  const positions = state?.items ?? [];
  const revision = state?.revision ?? 0;

  function submitUpsert() {
    setFormError(null);
    const parsedQuantity = Number(quantity);
    const parsedCost = Number(averageCost);
    if (!symbol.trim()) {
      setFormError('请输入标的代码');
      return;
    }
    if (!Number.isFinite(parsedQuantity) || parsedQuantity <= 0) {
      setFormError('数量必须大于 0');
      return;
    }
    if (!Number.isFinite(parsedCost) || parsedCost < 0) {
      setFormError('平均成本不能小于 0');
      return;
    }
    upsertMutation.mutate(
      {
        market,
        symbol: symbol.trim(),
        quantity: parsedQuantity,
        averageCost: parsedCost,
        expectedPortfolioRevision: revision,
      },
      {
        onSuccess: () => {
          setQuantity('');
          setAverageCost('');
        },
        onError: (error) => {
          const apiError = toApiError(error);
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
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pos-market">市场</Label>
          <select
            id="pos-market"
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
          <Input id="pos-symbol" placeholder="如 000001.SZ" value={symbol} onChange={(event) => setSymbol(event.target.value)} />
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
        <Button size="sm" disabled={upsertMutation.isPending} onClick={submitUpsert}>
          {upsertMutation.isPending ? '保存中…' : '新增/修改'}
        </Button>
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
              <div className="flex items-center gap-2 text-sm">
                <span style={{ color: 'var(--color-fg-muted)' }}>{position.market}</span>
                <span className="font-medium">{position.symbol}</span>
                <span>数量 {position.quantity}</span>
                <span>成本 {position.average_cost}</span>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setRemoveTarget(position)}>
                删除
              </Button>
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
