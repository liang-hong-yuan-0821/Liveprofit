import { useState } from 'react';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import { todayLocalDate } from '../../../shared/format/dateTime';
import { ConceptTreemap } from '../components/ConceptTreemap';
import { KLineDialog } from '../components/KLineDialog';
import type { TreemapNodeClick } from '../components/conceptTreeOption';
import { useConceptTreeQuery } from './queries';

// 板块区块（热门概念，板块概念Treemap方案 3.4）：treemap 两层展示——概念矩形
// 大小=热度、颜色=当日涨跌幅（红涨绿跌），成分股矩形大小=|当日涨跌幅|；
// 悬浮 tooltip 显示数值，点击节点弹窗 K 线（概念 → 板块指数、个股 → 个股日线）。
// 排名与热度由服务端读 market.sector_daily 现场计算（heat_v1，dc 源），前端不计算；
// 无热点（NO_HOT_CONCEPTS/空列表）是正常空态；STALE 可展示旧数据并标注。
// 日期选择器 = 榜单日期（from→as_of 透传，m6 定稿——现场计算支持任意历史日期）。
export function HotConceptsPanel() {
  const [asOf, setAsOf] = useState(todayLocalDate());
  const [selectedNode, setSelectedNode] = useState<TreemapNodeClick | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const query = useConceptTreeQuery({
    market: 'CN',
    interval: '1d',
    from: asOf,
    to: asOf,
  });
  const items = query.data?.items ?? [];
  const snapshot = query.data;
  const stale = snapshot?.freshness_status === 'STALE';

  const handleNodeClick = (node: TreemapNodeClick) => {
    setSelectedNode(node);
    setDialogOpen(true);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="hot-from">榜单日期</Label>
          <Input
            id="hot-from"
            type="date"
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5 pb-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          <span>市场 CN · 周期 1d · 热度 top 30</span>
          {snapshot && <span>榜单 {snapshot.as_of ?? '—'} · {snapshot.algorithm_version}</span>}
          {stale && <Badge variant="warning">数据可能延迟</Badge>}
        </div>
      </div>

      {query.isPending && <LoadingState label="热门概念加载中…" />}

      {query.isError && !query.data && (
        <ErrorState
          error={toApiError(query.error)}
          onRetry={toApiError(query.error).retryable ? () => void query.refetch() : undefined}
        />
      )}

      {snapshot && items.length === 0 && (
        <EmptyState title="当前条件下暂无热门概念" />
      )}

      {items.length > 0 && (
        <ConceptTreemap data={items} height={560} onNodeClick={handleNodeClick} />
      )}

      {selectedNode && (
        <KLineDialog
          node={selectedNode}
          open={dialogOpen}
          onOpenChange={(open) => {
            setDialogOpen(open);
            if (!open) setSelectedNode(null);  // 关闭即卸载，不留查询
          }}
        />
      )}
    </div>
  );
}
