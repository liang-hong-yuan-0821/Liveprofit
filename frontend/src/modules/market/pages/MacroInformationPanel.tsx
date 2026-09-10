import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Button } from '../../../shared/ui/button';
import { Card, CardContent } from '../../../shared/ui/card';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import { formatDateTime } from '../../../shared/format/dateTime';
import { useMacroInformationQuery } from './queries';

// 信息区块（宏观信息）：仅展示已审核投影；按服务端 Cursor 顺序消费；
// 空列表为正常空态，不从预测响应或报告文本拼装；卡片标题跳转事件研究
// （?event_id= 携带，用卡片已渲染字段预填草稿，绝不自动预测）。
export function MacroInformationPanel() {
  const [market, setMarket] = useState('');
  const [topic, setTopic] = useState('');

  const query = useMacroInformationQuery({ market: market || undefined, topic: topic || undefined });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  // 主题可搜索下拉：选项来自已加载数据去重（自由输入仍允许，服务端校验兜底）
  const topicOptions = useMemo(() => {
    const topics = new Set<string>();
    items.forEach((item) => {
      if (item.macro_topic) topics.add(item.macro_topic);
    });
    return [...topics];
  }, [items]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="macro-market">市场</Label>
          <select
            id="macro-market"
            className="h-9 rounded-md border bg-transparent px-3 text-sm"
            style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
            value={market}
            onChange={(event) => setMarket(event.target.value)}
          >
            <option value="">全部</option>
            <option value="US">US</option>
            <option value="KR">KR</option>
            <option value="CN">CN</option>
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="macro-topic">主题</Label>
          <Input
            id="macro-topic"
            list="macro-topic-options"
            placeholder="输入或选择主题"
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
          />
          <datalist id="macro-topic-options">
            {topicOptions.map((option) => (
              <option key={option} value={option} />
            ))}
          </datalist>
        </div>
      </div>

      {query.isPending && <LoadingState label="宏观信息加载中…" />}
      {query.isError && !query.data && (
        <ErrorState
          error={toApiError(query.error)}
          onRetry={toApiError(query.error).retryable ? () => void query.refetch() : undefined}
        />
      )}

      {query.data && items.length === 0 && (
        <EmptyState title="暂无可展示的事件研究宏观信息" />
      )}

      {items.length > 0 && (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.id}>
              <Card>
                <CardContent>
                  <Link
                    to={item.event_id !== null ? `/event-study?event_id=${item.event_id}` : '/event-study'}
                    state={
                      item.event_id !== null
                        ? {
                            prefill: {
                              // Q-03 已确认：无 prefill 接口，用卡片已渲染字段预填输入草稿
                              eventText: [item.title, item.summary].filter(Boolean).join('\n').slice(0, 20000),
                              eventType: item.macro_topic ?? undefined,
                              assetTicker:
                                item.related_assets?.length === 1 ? item.related_assets[0] : undefined,
                            },
                          }
                        : undefined
                    }
                    className="text-sm font-medium hover:underline"
                  >
                    {item.title}
                  </Link>
                  {item.summary && (
                    <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
                      {item.summary}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    <span>{formatDateTime(item.occurred_at)}</span>
                    {item.market_tags.map((tag) => (
                      <Badge key={tag} variant="outline">{tag}</Badge>
                    ))}
                    {item.macro_topic && <Badge variant="secondary">{item.macro_topic}</Badge>}
                    <span>来源：{item.source ?? '—'}</span>
                    {item.related_assets && item.related_assets.length > 0 && (
                      <span>关联资产：{item.related_assets.join('、')}</span>
                    )}
                    <span>研究状态：{item.research_status ?? '—'}</span>
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}

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
    </div>
  );
}
