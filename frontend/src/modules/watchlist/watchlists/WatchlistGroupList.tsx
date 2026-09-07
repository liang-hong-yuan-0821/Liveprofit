import { useState } from 'react';
import type { WatchlistDTO } from '../../../api/generated';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Button } from '../../../shared/ui/button';
import { ConfirmDialog } from '../../../shared/ui/ConfirmDialog';
import { Input } from '../../../shared/ui/input';
import {
  useCreateWatchlistMutation,
  useDeleteWatchlistMutation,
  useRenameWatchlistMutation,
  useWatchlistsQuery,
} from '../queries';

// 自选分组列表：新建/改名用行内编辑（Q-04 建议默认值），删除用确认弹窗；
// 名称冲突保留输入就地提示；非空分组阻止删除；404/409 后以服务端数据重渲染。
interface WatchlistGroupListProps {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

export function WatchlistGroupList({ selectedId, onSelect }: WatchlistGroupListProps) {
  const query = useWatchlistsQuery();
  const createMutation = useCreateWatchlistMutation();
  const renameMutation = useRenameWatchlistMutation();
  const deleteMutation = useDeleteWatchlistMutation();

  const [newName, setNewName] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<WatchlistDTO | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const createError = createMutation.error ? toApiError(createMutation.error) : null;
  const renameError = renameMutation.error ? toApiError(renameMutation.error) : null;
  const deleteError = deleteMutation.error ? toApiError(deleteMutation.error) : null;

  function submitCreate() {
    setFormError(null);
    if (!newName.trim()) {
      setFormError('请输入分组名称');
      return;
    }
    createMutation.mutate(newName.trim(), {
      onSuccess: () => setNewName(''),
      onError: (error) => {
        // 名称冲突保留输入就地提示
        const apiError = toApiError(error);
        if (apiError.code === 'WATCHLIST_NAME_CONFLICT') setFormError('分组名称已存在');
        else setFormError(apiError.message);
      },
    });
  }

  function submitRename() {
    setFormError(null);
    const target = query.data?.pages.flatMap((page) => page.items).find((group) => group.id === renamingId);
    if (!target || !renameValue.trim()) return;
    renameMutation.mutate(
      { watchlistId: target.id, name: renameValue.trim(), expectedVersion: target.version },
      {
        onSuccess: () => setRenamingId(null),
        onError: (error) => {
          const apiError = toApiError(error);
          setFormError(apiError.code === 'WATCHLIST_NAME_CONFLICT' ? '分组名称已存在' : apiError.message);
        },
      },
    );
  }

  function confirmDelete() {
    if (!deleteTarget) return;
    deleteMutation.mutate(
      { watchlistId: deleteTarget.id, expectedVersion: deleteTarget.version },
      {
        onSuccess: () => {
          setDeleteTarget(null);
          if (selectedId === deleteTarget.id) onSelect(null);
        },
        onError: (error) => {
          const apiError = toApiError(error);
          if (apiError.code === 'WATCHLIST_NOT_EMPTY') {
            setFormError('分组非空，请先处理标的后再删除');
          } else if (apiError.code !== 'RESOURCE_NOT_FOUND') {
            setFormError(apiError.message);
          }
          // 非空/资源不存在：保持弹窗关闭，冲突时列表已按服务端刷新
          setDeleteTarget(null);
        },
      },
    );
  }

  if (query.isPending) return <LoadingState label="自选分组加载中…" />;
  if (query.isError) {
    const error = toApiError(query.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void query.refetch() : undefined} />;
  }

  const groups = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Input
          placeholder="新建分组名称"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submitCreate();
          }}
          aria-label="新建分组名称"
        />
        <Button size="sm" disabled={createMutation.isPending} onClick={submitCreate}>
          {createMutation.isPending ? '创建中…' : '新建'}
        </Button>
      </div>

      {(formError || createError || renameError || deleteError) && (
        <p className="text-xs text-red-400">
          {formError ?? createError?.message ?? renameError?.message ?? deleteError?.message}
        </p>
      )}

      {groups.length === 0 && <EmptyState title="暂无自选分组" description="新建一个分组来组织关注标的" />}

      <ul className="flex flex-col gap-1">
        {groups.map((group) => (
          <li key={group.id}>
            <div
              className={`flex items-center justify-between gap-2 rounded-md border p-2 ${
                selectedId === group.id ? 'bg-[var(--color-surface)]' : ''
              }`}
              style={{ borderColor: 'var(--color-border)' }}
            >
              {renamingId === group.id ? (
                <div className="flex flex-1 gap-2">
                  <Input
                    aria-label="分组改名输入"
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
                    onClick={() => onSelect(group.id === selectedId ? null : group.id)}
                  >
                    <span className="font-medium">{group.name}</span>
                    <Badge variant="secondary">{group.item_count} 个标的</Badge>
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRenamingId(group.id);
                      setRenameValue(group.name);
                    }}
                  >
                    改名
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(group)}>
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
        title={`删除分组「${deleteTarget?.name ?? ''}」`}
        description="删除后分组与其中标的关系将被移除"
        pending={deleteMutation.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
