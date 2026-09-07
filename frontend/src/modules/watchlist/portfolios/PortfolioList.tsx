import { useState } from 'react';
import type { PortfolioDTO } from '../../../api/generated';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Button } from '../../../shared/ui/button';
import { ConfirmDialog } from '../../../shared/ui/ConfirmDialog';
import { Input } from '../../../shared/ui/input';
import {
  useCreatePortfolioMutation,
  useDeletePortfolioMutation,
  usePortfoliosQuery,
  useRenamePortfolioMutation,
} from '../queries';

// 组合列表：行内新建/改名 + 确认删除；非空组合阻止删除；冲突后以服务端数据重渲染。
interface PortfolioListProps {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

export function PortfolioList({ selectedId, onSelect }: PortfolioListProps) {
  const query = usePortfoliosQuery();
  const createMutation = useCreatePortfolioMutation();
  const renameMutation = useRenamePortfolioMutation();
  const deleteMutation = useDeletePortfolioMutation();

  const [newName, setNewName] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
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

  function submitRename() {
    setFormError(null);
    const target = query.data?.pages.flatMap((page) => page.items).find((portfolio) => portfolio.id === renamingId);
    if (!target || !renameValue.trim()) return;
    renameMutation.mutate(
      { portfolioId: target.id, name: renameValue.trim(), expectedVersion: target.version },
      {
        onSuccess: () => setRenamingId(null),
        onError: (error) => {
          const apiError = toApiError(error);
          setFormError(apiError.code === 'PORTFOLIO_NAME_CONFLICT' ? '组合名称已存在' : apiError.message);
        },
      },
    );
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
  if (query.isError) {
    const error = toApiError(query.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void query.refetch() : undefined} />;
  }

  const portfolios = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="flex flex-col gap-2">
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
              {renamingId === portfolio.id ? (
                <div className="flex flex-1 gap-2">
                  <Input
                    aria-label="组合改名输入"
                    value={renameValue}
                    onChange={(event) => setRenameValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') submitRename();
                      if (event.key === 'Escape') setRenamingId(null);
                    }}
                  />
                  <Button size="sm" onClick={submitRename}>
                    保存
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setRenamingId(null)}>
                    取消
                  </Button>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    className="flex flex-1 items-center gap-2 text-left text-sm"
                    onClick={() => onSelect(portfolio.id === selectedId ? null : portfolio.id)}
                  >
                    <span className="font-medium">{portfolio.name}</span>
                    <Badge variant="secondary">{portfolio.position_count} 个持仓</Badge>
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRenamingId(portfolio.id);
                      setRenameValue(portfolio.name);
                    }}
                  >
                    改名
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(portfolio)}>
                    删除
                  </Button>
                </>
              )}
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
