import { useState } from 'react';
import { Link } from 'react-router';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Button } from '../../../shared/ui/button';
import { ConfirmDialog } from '../../../shared/ui/ConfirmDialog';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import type { WatchlistItemDTO } from '../../../api/generated';
import {
  useAddWatchlistItemMutation,
  useRemoveWatchlistItemMutation,
  useReorderWatchlistItemsMutation,
  useWatchlistItemsQuery,
} from '../queries';

// 分组标的列表：新增（market + symbol）、上移/下移整表排序（PUT items/order）、删除；
// 重复标的不可乐观插入；排序/删除 409 后重新读取服务器顺序；404 失效父/子 Query。
export function WatchlistItemList({ watchlistId }: { watchlistId: string | null }) {
  const itemsQuery = useWatchlistItemsQuery(watchlistId);
  const addMutation = useAddWatchlistItemMutation(watchlistId ?? '');
  const reorderMutation = useReorderWatchlistItemsMutation(watchlistId ?? '');
  const removeMutation = useRemoveWatchlistItemMutation(watchlistId ?? '');

  const [market, setMarket] = useState('CN');
  const [symbol, setSymbol] = useState('');
  const [removeTarget, setRemoveTarget] = useState<WatchlistItemDTO | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  if (watchlistId === null) {
    return <EmptyState title="选择左侧分组查看标的" />;
  }

  if (itemsQuery.isPending) return <LoadingState label="标的列表加载中…" />;
  if (itemsQuery.isError) {
    const error = toApiError(itemsQuery.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void itemsQuery.refetch() : undefined} />;
  }

  const state = itemsQuery.data;
  const items = state?.items ?? [];
  const revision = state?.revision ?? 0;

  function submitAdd() {
    setFormError(null);
    if (!symbol.trim()) {
      setFormError('请输入标的代码');
      return;
    }
    addMutation.mutate(
      { market, symbol: symbol.trim(), expectedWatchlistRevision: revision },
      {
        onSuccess: () => setSymbol(''),
        onError: (error) => {
          const apiError = toApiError(error);
          setFormError(apiError.code === 'WATCHLIST_ITEM_DUPLICATE' ? '该标的已在分组中' : apiError.message);
        },
      },
    );
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    [next[index], next[target]] = [next[target], next[index]];
    // 整表重排：提交完整有序列表
    reorderMutation.mutate({
      expectedWatchlistRevision: revision,
      items: next.map((item, order) => ({ market: item.market, symbol: item.symbol, display_order: order })),
    });
  }

  function confirmRemove() {
    if (!removeTarget) return;
    removeMutation.mutate(
      { itemId: removeTarget.id, expectedWatchlistRevision: revision },
      {
        onSuccess: () => setRemoveTarget(null),
        onError: () => setRemoveTarget(null),
      },
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="item-market">市场</Label>
          <select
            id="item-market"
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
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="item-symbol">标的代码</Label>
          <Input
            id="item-symbol"
            placeholder="如 000001.SZ"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submitAdd();
            }}
          />
        </div>
        <Button size="sm" disabled={addMutation.isPending} onClick={submitAdd}>
          {addMutation.isPending ? '添加中…' : '添加'}
        </Button>
      </div>

      {formError && <p className="text-xs text-red-400">{formError}</p>}

      {items.length === 0 && <EmptyState title="尚未添加标的" />}

      <ul className="flex flex-col gap-1">
        {items.map((item, index) => (
          <li key={item.id}>
            <div
              className="flex items-center justify-between gap-2 rounded-md border p-2"
              style={{ borderColor: 'var(--color-border)' }}
            >
              <div className="flex items-center gap-2 text-sm">
                <span style={{ color: 'var(--color-fg-muted)' }}>{item.market}</span>
                <span className="font-medium">{item.symbol}</span>
                <Button asChild size="sm" variant="ghost">
                  <Link to={`/ai?create=1`}>AI 分析</Link>
                </Button>
              </div>
              <div className="flex items-center gap-1">
                <Button size="sm" variant="ghost" disabled={index === 0} onClick={() => move(index, -1)}>
                  ↑
                </Button>
                <Button size="sm" variant="ghost" disabled={index === items.length - 1} onClick={() => move(index, 1)}>
                  ↓
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRemoveTarget(item)}>
                  删除
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      <ConfirmDialog
        open={removeTarget !== null}
        title={`移除标的「${removeTarget?.market}:${removeTarget?.symbol}」`}
        description="移除后该标的将不在本分组中"
        confirmLabel="移除"
        pending={removeMutation.isPending}
        onConfirm={confirmRemove}
        onCancel={() => setRemoveTarget(null)}
      />
    </div>
  );
}
