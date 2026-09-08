import type { PendingEventDTO } from '../../../../../api/generated';

// 待审事件行视图模型：编辑态一律字符串，空串 = 该字段不落值（NULL）。
// 默认值取 ai_suggestions（AI 提取值）；importance 链：suggestions ?? importance_hint ?? 3。
export type PendingAction = 'skip' | 'approve' | 'ignore';

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

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numStr(value: unknown): string {
  return typeof value === 'number' && !Number.isNaN(value) ? String(value) : '';
}

function int(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) ? value : fallback;
}

export function toPendingEventRowVM(draft: PendingEventDTO): PendingEventRowVM {
  const suggestions = draft.ai_suggestions ?? {};
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
    expectedValue: numStr(suggestions.expected_value),
    actualValue: numStr(suggestions.actual_value),
    previousValue: numStr(suggestions.previous_value),
    action: 'skip',
    aiSuggestions: draft.ai_suggestions ?? null,
  };
}
