import { normalizeEventText } from './normalizeEventText';
import { EventReviewFields } from './EventReviewFields';
import { Button } from '../../../../shared/ui/button';
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
  onChange,
  disabled = false,
}: {
  open: boolean;
  vm: PendingEventRowVM | null;
  onClose: () => void;
  onChange?: (patch: Partial<PendingEventRowVM>) => void;
  disabled?: boolean;
}) {
  if (!vm) return null;
  // aiSuggestions 是自由 dict：unresolved_entities 需运行时守卫（Array.isArray + string 过滤）
  const unresolved = Array.isArray(vm.aiSuggestions?.unresolved_entities)
    ? vm.aiSuggestions.unresolved_entities.filter((x): x is string => typeof x === 'string')
    : [];
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>#{vm.draftId} {normalizeEventText(vm.title)}</DialogTitle>
          <DialogDescription>
            {vm.source}
            {vm.announcedAt ? ` · ${formatDateTime(vm.announcedAt)}` : ''}
            {vm.sourceUrl && /^https?:\/\//i.test(vm.sourceUrl) && <a href={vm.sourceUrl} target="_blank" rel="noopener noreferrer" className="ml-2 underline">查看原文 ↗</a>}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <section>
            <p className="mb-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              事件原文
            </p>
            <div className="rounded-md border p-3" style={{ borderColor: 'var(--color-border)' }}>
              {vm.content ? <MarkdownView content={normalizeEventText(vm.content)} imagePolicy="text" /> : <p className="text-sm">（无原文）</p>}
            </div>
          </section>
          <section>
            <p className="mb-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              AI 预填建议（请确认后提交）
            </p>
            {unresolved.length > 0 && (
              <p className="mb-2 text-sm text-amber-200">
                ⚠ 未解析目标：{unresolved.join('、')}——请人工补目标引用
                （SW:801080 / CONCEPT:BK1753.DC / stock:600519.SH）或回退 market
              </p>
            )}
            <dl className="mb-3 grid grid-cols-2 gap-2 text-sm"><dt>事件分类</dt><dd>{vm.eventType || '未分类'}</dd><dt>重要性</dt><dd>{vm.importance} / 5</dd><dt>影响范围</dt><dd>{vm.eventScope === 'market' ? '全市场' : vm.eventScope === 'sector' ? '行业 / 概念' : '个股'}</dd><dt>目标引用</dt><dd className="break-words">{vm.affectedScopeRefs || '无'}</dd></dl>
            <details><summary className="mb-2 text-xs text-[var(--color-fg-muted)]">原始 AI 数据</summary><pre className="overflow-x-auto rounded-md border p-3 text-xs" style={{ borderColor: 'var(--color-border)' }}>
              {vm.aiSuggestions ? JSON.stringify(vm.aiSuggestions, null, 2) : '（无）'}
            </pre></details>
          </section>
          {onChange && <section className="border-t border-[var(--color-border)] pt-4"><h3 className="mb-3 text-sm font-semibold">审核字段</h3><EventReviewFields vm={vm} disabled={disabled} onChange={onChange} /><p className="mt-3 text-xs text-[var(--color-fg-muted)]">修改会保留在当前列表草稿中，尚未提交。返回列表后统一批量提交。</p></section>}
          <div className="sticky -bottom-6 -mx-6 flex justify-end border-t border-[var(--color-border)] bg-[var(--color-bg)] px-6 py-3"><Button variant="outline" onClick={onClose}>返回列表</Button></div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
