import { useState } from 'react';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import { ConceptTreemap } from '../components/ConceptTreemap';
import { KLineDialog } from '../components/KLineDialog';
import { pctColor, pctText, type TreemapNodeClick } from '../components/conceptTreeOption';
import { useConceptTreeQuery } from './queries';

// 板块区块（热门概念，板块概念Treemap方案 3.4）：treemap 两层展示——概念矩形
// 大小=热度、颜色=当日涨跌幅（红涨绿跌），成分股矩形大小=|当日涨跌幅|；
// 悬浮 tooltip 显示数值，点击节点弹窗 K 线（概念 → 板块指数、个股 → 个股日线）。
// 排名与热度由服务端读 market.sector_daily 现场计算（heat_v1，dc 源），前端不计算；
// 无热点（NO_HOT_CONCEPTS/空列表）是正常空态；STALE 可展示旧数据并标注。
// 默认最近可展示日；显式选择日期后保留历史榜单，不随后台任务跳回最新。
export function HotConceptsPanel() {
  const [listOpen, setListOpen] = useState(false);
  const [asOf, setAsOf] = useState('');
  const [selectedNode, setSelectedNode] = useState<TreemapNodeClick | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const query = useConceptTreeQuery({
    market: 'CN',
    interval: '1d',
    asOf: asOf || undefined,
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
        <button type="button" className="pb-2 text-xs text-[var(--color-accent-bright)]" onClick={() => setAsOf('')}>最近可展示日</button>
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

      {items.length > 0 && <>
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-xs text-[var(--color-fg-muted)]">
          行情覆盖 {snapshot?.coverage?.boards.available_count ?? items.filter(item => item.pct_chg != null).length}/{snapshot?.coverage?.boards.expected_count ?? items.length} 个概念 · 灰色表示无行情，不能据此判断涨跌
          {items.every(item => item.pct_chg == null) && <p className="mt-2 font-medium text-amber-500">所选日期 {snapshot?.as_of ?? asOf ?? '未知'} 的概念均缺少行情。响应榜单日期 {snapshot?.as_of ?? '未知'}；可选择其他日期查看。</p>}
        </div>
        <ConceptTreemap data={items} height={560} onNodeClick={handleNodeClick} />
        <details onToggle={event => setListOpen(event.currentTarget.open)} className="rounded-xl border border-[var(--color-border)] p-4">
          <summary className="text-sm font-medium">概念列表 · 完整名称与涨跌幅</summary>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {listOpen && items.map(item => <button type="button" key={item.sector_code} onClick={() => handleNodeClick({ kind: 'concept', code: item.sector_code, name: item.sector_name })} className="flex items-center justify-between gap-3 rounded-lg p-2 text-left text-xs hover:bg-[var(--color-surface)]"><span>{item.sector_name}<span className="ml-2 text-[var(--color-fg-muted)]">{item.sector_code}</span></span><span className="shrink-0 tabular-nums" style={{ color: pctColor(item.pct_chg) }}>{pctText(item.pct_chg)} ↗</span></button>)}
          </div>
        </details>
      </>}

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
