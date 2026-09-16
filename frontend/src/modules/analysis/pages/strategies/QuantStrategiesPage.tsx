// 量化策略管理页（plan 4.4.1）：列表/创建/草稿编辑/版本历史/发布/归档。
// 脚本仅等宽 textarea 纯文本展示；422 校验错误（STRATEGY_VALIDATION_FAILED）展示 detail 文本。

import { useState } from 'react';

import { ApiError, toApiError } from '../../../../api/client';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../../../../shared/ui/dialog';
import { Input } from '../../../../shared/ui/input';
import { Label } from '../../../../shared/ui/label';
import type { QuantStrategyDTO, QuantStrategyDraftDTO, QuantStrategyVersionDTO } from '../../../../api/generated';
import {
  useArchiveVersionMutation,
  useCreateQuantStrategyMutation,
  useListQuantStrategies,
  usePublishVersionMutation,
  useQuantStrategyDraft,
  useSaveDraftMutation,
} from './queries';
import { StrategyCodeEditor } from './StrategyCodeEditor';

const STATUS_LABELS: Record<string, string> = { DRAFT: '草稿', PUBLISHED: '已发布', ARCHIVED: '已归档' };

export default function QuantStrategiesPage() {
  const strategies = useListQuantStrategies();
  const [editingId, setEditingId] = useState<string | null>(null);

  if (strategies.isLoading) return <p className="p-4 text-sm">加载中…</p>;
  if (strategies.isError) {
    return <ErrorState error={toApiError(strategies.error)} onRetry={() => void strategies.refetch()} />;
  }

  return (
    <div className="flex flex-col gap-4 p-4" aria-label="量化策略管理">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">量化策略</h2>
        <CreateStrategyDialog />
      </div>
      {(strategies.data ?? []).length === 0 && (
        <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          暂无策略。点击「新建策略」创建版本化条件脚本（仅已发布版本可被任务引用）。
        </p>
      )}
      {(strategies.data ?? []).map((s) => (
        <Card key={s.id}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {s.name}
              <span className="text-xs font-normal" style={{ color: 'var(--color-fg-muted)' }}>
                v{Math.max(...s.versions.map((v: QuantStrategyVersionDTO) => v.version_no), 0)} · 元数据版本 {s.version}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {s.description && <p className="text-sm">{s.description}</p>}
            <StrategyVersionList strategy={s} />
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setEditingId(s.id)}>
                编辑草稿
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
      {editingId &&
        (() => {
          const target = (strategies.data ?? []).find((s) => s.id === editingId);
          return target ? <StrategyEditorDialog strategy={target} onClose={() => setEditingId(null)} /> : null;
        })()}
    </div>
  );
}

function CreateStrategyDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [source, setSource] = useState('');
  const mutation = useCreateQuantStrategyMutation();

  function submit() {
    mutation.mutate(
      { name, description: description || null, source_code: source },
      {
        onSuccess: () => {
          setOpen(false);
          setName('');
          setDescription('');
          setSource('');
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>新建策略</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建策略</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <Label htmlFor="new-name">名称</Label>
          <Input id="new-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} />
          <Label htmlFor="new-desc">描述（可选）</Label>
          <Input id="new-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          <StrategyCodeEditor value={source} onChange={setSource} ariaLabel="新建策略代码" />
          {!source.trim() && (
            <p className="text-xs text-red-400" role="alert">请先输入策略代码（def strategy(context): 返回七键字典）</p>
          )}
          {mutation.isError && <ErrorState error={toApiError(mutation.error)} />}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button disabled={!name.trim() || !source.trim() || mutation.isPending} onClick={submit}>创建</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StrategyVersionList({ strategy }: { strategy: QuantStrategyDTO }) {
  const publish = usePublishVersionMutation(strategy.id);
  const archive = useArchiveVersionMutation(strategy.id);
  const sorted = [...strategy.versions].sort((a, b) => b.version_no - a.version_no);

  return (
    <div className="flex flex-col gap-1" aria-label="版本历史">
      {sorted.map((v) => (
        <div key={v.id} className="flex items-center justify-between rounded-md border p-2 text-sm">
          <span className="flex items-center gap-2">
            <Badge variant={v.status === 'PUBLISHED' ? 'default' : 'outline'}>
              {STATUS_LABELS[v.status] ?? v.status}
            </Badge>
            <span>v{v.version_no}</span>
            <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              {v.source_hash.slice(0, 12)}
            </span>
            {v.published_at && (
              <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                发布于 {formatDateTime(v.published_at)}
              </span>
            )}
          </span>
          <span className="flex gap-2">
            {v.status === 'DRAFT' && (
              <Button
                size="sm"
                disabled={publish.isPending}
                onClick={() => publish.mutate({ versionId: v.id, expectedVersion: v.version })}
              >
                发布
              </Button>
            )}
            {v.status === 'PUBLISHED' && (
              <Button
                size="sm"
                variant="outline"
                disabled={archive.isPending}
                onClick={() => archive.mutate(v.id)}
              >
                归档
              </Button>
            )}
          </span>
        </div>
      ))}
      {(publish.isError || archive.isError) && (
        <ErrorState error={toApiError(publish.error ?? archive.error)} />
      )}
    </div>
  );
}

function StrategyEditorDialog({ strategy, onClose }: { strategy: QuantStrategyDTO; onClose: () => void }) {
  const draft = useQuantStrategyDraft(strategy.id);
  const save = useSaveDraftMutation(strategy.id);
  const [source, setSource] = useState<string | null>(null);
  const [name, setName] = useState(strategy.name);
  const [description, setDescription] = useState(strategy.description ?? '');

  if (draft.isLoading) return null;
  if (draft.isError) {
    return (
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent>
          <p className="text-sm text-red-400" role="alert">草稿加载失败，请刷新页面重试</p>
          <div className="flex justify-end">
            <Button variant="outline" onClick={onClose}>关闭</Button>
          </div>
        </DialogContent>
      </Dialog>
    );
  }
  const dto: QuantStrategyDraftDTO | undefined = draft.data;
  const currentSource = source ?? dto?.source_code ?? '';
  const error = save.error instanceof ApiError ? save.error : null;
  const isValidationError = error?.code === 'STRATEGY_VALIDATION_FAILED';

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>编辑草稿（v{dto?.version_no}）</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <Label htmlFor="draft-name">名称</Label>
          <Input id="draft-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} />
          <Label htmlFor="draft-desc">描述（可选）</Label>
          <Input id="draft-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          <StrategyCodeEditor value={currentSource} onChange={setSource} ariaLabel="策略脚本编辑器" />
          {isValidationError && (
            <p className="text-xs text-red-400" role="alert">
              校验失败：{error?.message}
            </p>
          )}
          {error && !isValidationError && <ErrorState error={toApiError(save.error)} />}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>关闭</Button>
            <Button
              disabled={save.isPending || dto === undefined}
              onClick={() => {
                if (dto === undefined) return;
                save.mutate(
                  {
                    name: name.trim() || strategy.name,
                    description: description || null,
                    source_code: currentSource,
                    expected_strategy_version: strategy.version,
                    expected_draft_version: dto.version,
                  },
                  { onSuccess: onClose },
                );
              }}
            >
              保存草稿
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

