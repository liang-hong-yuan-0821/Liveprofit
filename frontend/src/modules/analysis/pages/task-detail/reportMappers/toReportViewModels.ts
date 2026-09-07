import type { ReportDTO, ReportSectionDTO } from '../../../../../api/generated';

// 报告 DTO → 展示 ViewModel（首期只服务任务详情页）。
// 区块固定顺序 market → sector → stock → decision；仅保留契约内区块，状态字段原样透传，
// 不依据字段缺失推断三态（API 契约 §六）。
export const REPORT_BLOCK_ORDER = ['market', 'sector', 'stock', 'decision'] as const;
export type ReportBlockKey = (typeof REPORT_BLOCK_ORDER)[number];

export interface ReportSectionViewModel {
  block: string;
  status: string;
  title: string;
  summary: string | null;
  content: string | null;
  charts: unknown[] | null;
  unavailableReason: string | null;
  retryable: boolean | null;
}

export interface ReportViewModel {
  schemaVersion: string;
  reportVersion: number;
  generatedAt: string;
  task: ReportDTO['task'];
  sections: ReportSectionViewModel[];
  dataSources: ReportDTO['data_sources'];
  riskNote: string | null;
}

export function toReportViewModel(dto: ReportDTO): ReportViewModel {
  const byBlock = new Map<string, ReportSectionDTO>(dto.sections.map((section) => [section.block as string, section]));
  return {
    schemaVersion: dto.schema_version,
    reportVersion: dto.report_version,
    generatedAt: dto.generated_at,
    task: dto.task,
    sections: REPORT_BLOCK_ORDER.filter((block) => byBlock.has(block)).map((block) =>
      mapSection(byBlock.get(block) as ReportSectionDTO),
    ),
    dataSources: dto.data_sources ?? null,
    riskNote: dto.risk_note ?? null,
  };
}

function mapSection(section: ReportSectionDTO): ReportSectionViewModel {
  return {
    block: section.block,
    status: section.status,
    title: section.title ?? section.block,
    summary: section.summary ?? null,
    content: section.content ?? null,
    charts: (section.charts as unknown[] | null) ?? null,
    unavailableReason: section.unavailable_reason ?? null,
    retryable: section.retryable ?? null,
  };
}

/** 折叠态预览：优先 summary；无 summary 时取正文首段截断（约 max 字符） */
export function truncateContentPreview(content: string | null, max = 120): string {
  if (!content) return '';
  const firstParagraph = content.split(/\n{2,}/)[0] ?? content;
  const trimmed = firstParagraph.replace(/\s+/g, ' ').trim();
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}
