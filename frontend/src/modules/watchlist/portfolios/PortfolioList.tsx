import { useState } from 'react';
import type { PortfolioDTO } from '../../../api/generated';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Button } from '../../../shared/ui/button';
import { ConfirmDialog } from '../../../shared/ui/ConfirmDialog';
import { Input } from '../../../shared/ui/input';
import {
  useCreatePortfolioMutation,
  useDeletePortfolioMutation,
  usePortfoliosQuery,
} from '../queries';
import { Badge } from '../../../shared/ui/badge';
import { PortfolioSettingsDialog } from './PortfolioSettingsDialog';

// 组合列表：行内新建/改名 + 确认删除；非空组合阻止删除；冲突后以服务端数据重渲染。
interface PortfolioListProps {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

export function PortfolioList({ selectedId, onSelect }: PortfolioListProps) {
  const query = usePortfoliosQuery();
  const createMutation = useCreatePortfolioMutation();
  const deleteMutation = useDeletePortfolioMutation();

  const [newName, setNewName] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<PortfolioDTO | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function submitCreate() {
    setFormError(null);
    if (!newName.trim()) {
      setFormError('请输入组合名称');
      return;
    }
    createMutation.mutate(newName.trim(), {
      onSuccess: () => setNewName(''),
      onError: (error) => {
        const apiError = toApiError(error);
        setFormError(apiError.code === 'PORTFOLIO_NAME_CONFLICT' ? '组合名称已存在' : apiError.message);
      },
    });
  }

  function confirmDelete() {
    if (!deleteTarget) return;
    deleteMutation.mutate(
      { portfolioId: deleteTarget.id, expectedVersion: deleteTarget.version },
      {
        onSuccess: () => {
          setDeleteTarget(null);
          if (selectedId === deleteTarget.id) onSelect(null);
        },
        onError: (error) => {
          const apiError = toApiError(error);
          if (apiError.code === 'PORTFOLIO_NOT_EMPTY') {
            setFormError('组合非空，请先删除全部持仓');
          } else if (apiError.code !== 'RESOURCE_NOT_FOUND') {
            setFormError(apiError.message);
          }
          setDeleteTarget(null);
        },
      },
    );
  }

  if (query.isPending) return <LoadingState label="组合列表加载中…" />;
  if (query.isError && !query.data) {
    const error = toApiError(query.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void query.refetch() : undefined} />;
  }

  const portfolios = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="flex flex-col gap-2">
      {query.isError && <div role="status" className="flex items-center gap-2 text-xs text-amber-500">组合更新失败，仍显示上次数据，编辑草稿已保留。<Button size="sm" variant="ghost" onClick={() => void query.refetch()}>重新加载</Button></div>}
      <div className="flex gap-2">
        <Input
          placeholder="新建组合名称"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submitCreate();
          }}
          aria-label="新建组合名称"
        />
        <Button size="sm" disabled={createMutation.isPending} onClick={submitCreate}>
          {createMutation.isPending ? '创建中…' : '新建'}
        </Button>
      </div>

      {formError && <p className="text-xs text-red-400">{formError}</p>}

      {portfolios.length === 0 && <EmptyState title="暂无组合" description="新建组合来维护手工持仓" />}

      <ul className="flex flex-col gap-1">
        {portfolios.map((portfolio) => (
          <li key={portfolio.id}>
            <div
              className={`flex items-center justify-between gap-2 rounded-md border p-2 ${
                selectedId === portfolio.id ? 'bg-[var(--color-surface)]' : ''
              }`}
              style={{ borderColor: 'var(--color-border)' }}
            >
              <button
                type="button"
                className="flex min-w-0 flex-1 flex-col gap-0.5 text-left"
                aria-pressed={selectedId === portfolio.id}
                onClick={() => onSelect(portfolio.id === selectedId ? null : portfolio.id)}
              >
                <span className="text-sm font-medium">
                  {portfolio.name} <Badge variant="secondary">{portfolio.position_count} 个持仓</Badge>
                </span>
                <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                  资产 {portfolio.total_assets?.toLocaleString()} · 现金 {portfolio.available_cash?.toLocaleString()} · 单笔风险 {Number((portfolio.risk_per_trade_pct * 100).toPrecision(6))}%
                  （任务仅读取提交时快照）
                </span>
              </button>
              <div className="flex items-center gap-2">
                <PortfolioSettingsDialog
                  portfolio={portfolio}
                  trigger={
                    <Button size="sm" variant="outline" aria-label="组合设置">
                      设置
                    </Button>
                  }
                />
                <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(portfolio)}>
                  删除
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="outline"
            size="sm"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? '加载中…' : '加载更多'}
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`删除组合「${deleteTarget?.name ?? ''}」`}
        description="删除后组合与持仓记录将被移除"
        pending={deleteMutation.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
