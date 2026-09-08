import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../../../shared/ui/dialog';
import { MarkdownView } from '../../../../shared/ui/markdown';
import { formatDateTime } from '../../../../shared/format/dateTime';
import type { PendingEventRowVM } from './mappers/toPendingEventRowVM';

// 待审事件原文详情：content 经 MarkdownView 渲染（统一 markdown 组件），
// AI 建议按 kind=json 惯例用格式化 pre 展示。
export function EventDetailDialog({
  open,
  vm,
  onClose,
}: {
  open: boolean;
  vm: PendingEventRowVM | null;
  onClose: () => void;
}) {
  if (!vm) return null;
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>#{vm.draftId} {vm.title}</DialogTitle>
          <DialogDescription>
            {vm.source}
            {vm.announcedAt ? ` · ${formatDateTime(vm.announcedAt)}` : ''}
            {vm.sourceUrl ? ` · 原文链接：${vm.sourceUrl}` : ''}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <section>
            <p className="mb-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              事件原文
            </p>
            <div className="rounded-md border p-3" style={{ borderColor: 'var(--color-border)' }}>
              {vm.content ? <MarkdownView content={vm.content} /> : <p className="text-sm">（无原文）</p>}
            </div>
          </section>
          <section>
            <p className="mb-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              AI 预填建议（请确认后提交）
            </p>
            <pre className="overflow-x-auto rounded-md border p-3 text-xs" style={{ borderColor: 'var(--color-border)' }}>
              {vm.aiSuggestions ? JSON.stringify(vm.aiSuggestions, null, 2) : '（无）'}
            </pre>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
