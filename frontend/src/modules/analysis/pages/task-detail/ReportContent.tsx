import { useState } from 'react';
import { Link } from 'react-router';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { MarkdownView } from '../../../../shared/ui/markdown';
import { formatDateTime } from '../../../../shared/format/dateTime';
import {
  truncateContentPreview,
  type ReportSectionViewModel,
  type ReportViewModel,
} from './reportMappers/toReportViewModels';

// 最新报告区：仅在 SUCCEEDED 后由页面渲染。区块按 DTO 显式 status 呈现三态；
// 折叠策略（Q-06 已确认）：decision 默认展开，market/sector/stock 默认折叠，
// 折叠态显示服务端 summary（无 summary 则正文首段截断预览）。
export function ReportContent({ report, onRetryReport }: { report: ReportViewModel; onRetryReport?: () => void }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ decision: true });

  function toggle(block: string) {
    setExpanded((prev) => ({ ...prev, [block]: !prev[block] }));
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>最新报告</CardTitle>
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            版本 {report.reportVersion} · 生成于 {formatDateTime(report.generatedAt)}
          </span>
        </CardHeader>
        <CardContent>
          {report.dataSources && report.dataSources.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {report.dataSources.map((source, index) => (
                <span key={index} className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                  {source.label}：{source.source}（as_of {source.as_of}）
                </span>
              ))}
            </div>
          )}
          {report.riskNote && (
            <p className="rounded-md border p-2 text-xs" style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg-muted)' }}>
              {report.riskNote}
            </p>
          )}
        </CardContent>
      </Card>

      {report.sections.map((section) => (
        <ReportSectionCard
          key={section.block}
          section={section}
          expanded={Boolean(expanded[section.block])}
          onToggle={() => toggle(section.block)}
          onRetryReport={onRetryReport}
        />
      ))}
    </div>
  );
}

function ReportSectionCard({
  section,
  expanded,
  onToggle,
  onRetryReport,
}: {
  section: ReportSectionViewModel;
  expanded: boolean;
  onToggle: () => void;
  onRetryReport?: () => void;
}) {
  if (section.status === 'UNAVAILABLE') {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{section.title}</CardTitle>
          <Badge variant="warning">不可用</Badge>
        </CardHeader>
        <CardContent>
          <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            {section.unavailableReason ?? '服务端未提供不可用原因'}
          </p>
          {section.retryable && onRetryReport && (
            <div className="mt-2">
              <Button variant="outline" size="sm" onClick={onRetryReport}>
                重新读取报告
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    );
  }

  if (section.status === 'NOT_REQUESTED') {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{section.title}</CardTitle>
          <Badge variant="secondary">未请求</Badge>
        </CardHeader>
        <CardContent>
          <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            本次分析未请求此区块。
          </p>
          <div className="mt-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/ai?create=1">新建包含该层级的分析</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  // AVAILABLE
  const summary = section.summary;
  const preview = summary ?? truncateContentPreview(section.content);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{section.title}</CardTitle>
        <Badge variant="success">可用</Badge>
      </CardHeader>
      <CardContent>
        {expanded ? (
          <>
            {section.content && <MarkdownView content={section.content} />}
            {/* charts：首期无通用报告图表渲染器，数据保留暂不渲染 */}
          </>
        ) : (
          <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            {preview || '（无摘要）'}
          </p>
        )}
        <div>
          <Button variant="ghost" size="sm" onClick={onToggle}>
            {expanded ? '收起' : '展开全文'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
