import type { PendingEventDTO } from '../../../../../api/generated';

// 待审事件行视图模型：编辑态一律字符串，空串 = 该字段不落值（NULL）。
// 默认值取 ai_suggestions（AI 提取值）；importance 链：suggestions ?? importance_hint ?? 3。
// 三级路由（方案第三章）：eventScope 链 suggestions.event_scope ?? 'market'；
// affectedScopeRefs 为逗号分隔文本（仅 sector/stock 回填 AI 建议）。
export type PendingAction = 'skip' | 'approve' | 'ignore';
export type PendingScope = 'market' | 'sector' | 'stock';

export interface PendingEventRowVM {
  draftId: number;
  announcedAt: string;
  source: string;
  title: string;
  content: string;
  sourceUrl: string | null;
  eventType: string;
  eventSubtype: string;
  eventCondition: string;
  importance: number;
  eventScope: PendingScope;
  affectedScopeRefs: string;
  expectedValue: string;
  actualValue: string;
  previousValue: string;
  action: PendingAction;
  aiSuggestions: Record<string, unknown> | null;
}

export const CONDITION_OPTIONS = ['', '超预期', '符合预期', '低于预期', '利好', '利空', '中性'] as const;
// 提交值必须是英文键（approve/ignore/skip）——中文仅作展示标签
export const ACTION_CHOICES: ReadonlyArray<{ value: PendingAction; label: string }> = [
  { value: 'skip', label: '跳过' },
  { value: 'approve', label: '通过' },
  { value: 'ignore', label: '忽略' },
];
export const ACTION_LABELS: Record<PendingAction, string> = {
  skip: '跳过',
  approve: '通过',
  ignore: '忽略',
};
// 作用域选项（提交值为英文键，中文仅作展示标签）
export const SCOPE_CHOICES: ReadonlyArray<{ value: PendingScope; label: string }> = [
  { value: 'market', label: 'market 全市场' },
  { value: 'sector', label: 'sector 行业/概念' },
  { value: 'stock', label: 'stock 个股' },
];
export const SCOPE_VALUES: readonly PendingScope[] = ['market', 'sector', 'stock'];

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numStr(value: unknown): string {
  return typeof value === 'number' && !Number.isNaN(value) ? String(value) : '';
}

function int(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) ? value : fallback;
}

function scope(value: unknown): PendingScope {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return (SCOPE_VALUES as readonly string[]).includes(text) ? (text as PendingScope) : 'market';
}

/** AI 建议引用数组 → 逗号分隔编辑文本（非数组/元素非字符串一律忽略）。 */
function refsText(value: unknown): string {
  if (!Array.isArray(value)) return '';
  return value
    .filter((item): item is string => typeof item === 'string' && item.trim() !== '')
    .map((item) => item.trim())
    .join(', ');
}

/** 编辑文本 → 提交用引用数组（中英文逗号/换行分隔；空项丢弃）。 */
export function parseScopeRefs(text: string): string[] {
  return text
    .split(/[,，;；\n]+/)
    .map((item) => item.trim())
    .filter((item) => item !== '');
}

/** 下层作用域缺目标（阻止提交的判定，与后端校验同规则）。 */
export function routeTargetMissing(vm: Pick<PendingEventRowVM, 'eventScope' | 'affectedScopeRefs'>): boolean {
  return vm.eventScope !== 'market' && parseScopeRefs(vm.affectedScopeRefs).length === 0;
}

export function toPendingEventRowVM(draft: PendingEventDTO): PendingEventRowVM {
  const suggestions = draft.ai_suggestions ?? {};
  const draftScope = scope(suggestions.event_scope);
  return {
    draftId: draft.draft_id,
    announcedAt: draft.announced_at ?? '',
    source: draft.source ?? '',
    title: draft.title,
    content: draft.content ?? '',
    sourceUrl: draft.source_url ?? null,
    eventType: str(suggestions.event_type),
    eventSubtype: str(suggestions.event_subtype),
    eventCondition: str(suggestions.event_condition),
    importance: int(suggestions.importance, int(draft.importance_hint, 3)),
    // 下层作用域才回填 AI 目标建议（market 固定无目标）
    eventScope: draftScope,
    affectedScopeRefs: draftScope === 'market' ? '' : refsText(suggestions.affected_scope_refs),
    expectedValue: numStr(suggestions.expected_value),
    actualValue: numStr(suggestions.actual_value),
    previousValue: numStr(suggestions.previous_value),
    action: 'skip',
    aiSuggestions: draft.ai_suggestions ?? null,
  };
}
