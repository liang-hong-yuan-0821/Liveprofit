import { Button } from '../../../../shared/ui/button';
import { Input } from '../../../../shared/ui/input';
import { formatDateTime } from '../../../../shared/format/dateTime';
import {
  ACTION_CHOICES, CONDITION_OPTIONS, SCOPE_CHOICES,
  type PendingEventRowVM, type PendingScope,
} from './mappers/toPendingEventRowVM';

// 待审事件单行：批量可编辑表格的行（grid 行 div，先例 WatchlistItemList）。
export const PENDING_ROW_GRID =
  'grid grid-cols-[3rem_9rem_7rem_minmax(0,1.2fr)_7rem_7rem_8rem_5rem_8rem_12rem_7rem_7rem_7rem_6rem] items-center gap-2';

const selectClass =
  'h-9 w-full rounded-md border bg-transparent px-2 text-sm outline-none focus-visible:border-[var(--color-accent)]';

export function PendingEventRow({
  vm,
  disabled = false,
  onChange,
  onShowDetail,
}: {
  vm: PendingEventRowVM;
  disabled?: boolean;
  onChange: (patch: Partial<PendingEventRowVM>) => void;
  onShowDetail: () => void;
}) {
  const label = (field: string) => `第${vm.draftId}行-${field}`;
  return (
    <div className={`${PENDING_ROW_GRID} border-b py-2`} style={{ borderColor: 'var(--color-border)' }}>
      <span className="flex items-center gap-1">
        <Button size="sm" variant="ghost" aria-label={label('查看原文')} onClick={onShowDetail}>
          详情
        </Button>
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          {vm.draftId}
        </span>
      </span>
      <span className="truncate text-xs" title={vm.announcedAt}>
        {formatDateTime(vm.announcedAt)}
      </span>
      <span className="truncate text-xs">{vm.source}</span>
      <span className="truncate text-sm" title={vm.title}>
        {vm.title}
      </span>
      <Input
        aria-label={label('事件类型')}
        value={vm.eventType}
        disabled={disabled}
        onChange={(e) => onChange({ eventType: e.target.value })}
        placeholder="宏观数据 / 央行 / 地缘…"
      />
      <Input
        aria-label={label('事件子类型')}
        value={vm.eventSubtype}
        disabled={disabled}
        onChange={(e) => onChange({ eventSubtype: e.target.value })}
        placeholder="CPI / LPR…"
      />
      <select
        aria-label={label('关键条件')}
        className={selectClass}
        style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
        value={vm.eventCondition}
        disabled={disabled}
        onChange={(e) => onChange({ eventCondition: e.target.value })}
      >
        {CONDITION_OPTIONS.map((o) => (
          <option key={o} value={o}>
            {o || '（未选）'}
          </option>
        ))}
      </select>
      <select
        aria-label={label('重要性')}
        className={selectClass}
        style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
        value={vm.importance}
        disabled={disabled}
        onChange={(e) => onChange({ importance: Number(e.target.value) })}
      >
        {[1, 2, 3, 4, 5].map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
      <select
        aria-label={label('作用域')}
        className={selectClass}
        style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
        value={vm.eventScope}
        disabled={disabled}
        title="AI 预填，请确认（人工可改）"
        onChange={(e) => onChange({ eventScope: e.target.value as PendingScope })}
      >
        {SCOPE_CHOICES.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
      <Input
        aria-label={label('目标')}
        value={vm.affectedScopeRefs}
        disabled={disabled}
        // 目标引用为逗号分隔文本：SW:801080 / CONCEPT:BK1753.DC / stock:600519.SH
        title="AI 预填，请确认（人工可改）：行业 SW:801080 / 概念 CONCEPT:BK1753.DC / 个股 stock:600519.SH"
        placeholder={vm.eventScope === 'market' ? 'market 无目标' : 'SW:801080, stock:600519.SH'}
        onChange={(e) => onChange({ affectedScopeRefs: e.target.value })}
      />
      <Input
        aria-label={label('预期值')}
        type="number"
        step="any"
        value={vm.expectedValue}
        disabled={disabled}
        onChange={(e) => onChange({ expectedValue: e.target.value })}
      />
      <Input
        aria-label={label('实际值')}
        type="number"
        step="any"
        value={vm.actualValue}
        disabled={disabled}
        onChange={(e) => onChange({ actualValue: e.target.value })}
      />
      <Input
        aria-label={label('前值')}
        type="number"
        step="any"
        value={vm.previousValue}
        disabled={disabled}
        onChange={(e) => onChange({ previousValue: e.target.value })}
      />
      <select
        aria-label={label('操作')}
        className={selectClass}
        style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
        value={vm.action}
        disabled={disabled}
        onChange={(e) => onChange({ action: e.target.value as PendingEventRowVM['action'] })}
      >
        {ACTION_CHOICES.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    </div>
  );
}
