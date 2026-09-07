import { Link, useLocation, useSearchParams } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Button } from '../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import { PredictionForm, type PredictionPrefill } from './PredictionForm';
import { PredictionResultPanel } from './PredictionResultPanel';
import { useEventStudyAssetsQuery, usePredictionMutation } from './queries';
import { toPredictionViewModel } from './mappers/toPredictionViewModel';

// 事件研究：选股/个股研究的辅助工具。请求是同步、有容量限制的一次性操作，
// 不创建分析任务、不进入任务时间线、不保存为全局状态，结果不伪装为分析报告。
// 宏观信息卡片跳转（?event_id= + 站内 state）仅预填输入草稿，绝不自动提交。
interface PrefillLocationState {
  prefill?: {
    eventText?: string;
    eventType?: string;
    assetTicker?: string;
  };
}

export default function AiEventStudyPage() {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const eventId = searchParams.get('event_id');

  const assetsQuery = useEventStudyAssetsQuery();
  const mutation = usePredictionMutation();

  const prefill: PredictionPrefill = (location.state as PrefillLocationState | null)?.prefill ?? {};

  return (
    <main className="flex flex-col gap-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">事件研究</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            基于历史事件样本，对目标资产做一次同步影响预测（不创建分析任务）
            {eventId ? ` · 关联事件 #${eventId}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link to="/market">
              <ArrowLeft className="size-4" aria-hidden />
              返回大盘
            </Link>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link to="/ai">返回 AI 投研看板</Link>
          </Button>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>预测输入</CardTitle>
        </CardHeader>
        <CardContent>
          {/* 资产清单是辅助能力：接口缺失/失败时保留自由输入，不阻断预测主流程（契约 §7.2） */}
          {assetsQuery.isPending && <LoadingState label="资产清单加载中…" />}
          {assetsQuery.isError && (
            <p className="mb-2 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              资产清单暂不可用，可自由输入目标资产代码
            </p>
          )}
          <PredictionForm
            assets={assetsQuery.data?.items ?? []}
            prefill={prefill}
            mutation={mutation}
          />
        </CardContent>
      </Card>

      {mutation.data && (
        <PredictionResultPanel result={toPredictionViewModel(mutation.data)} />
      )}
    </main>
  );
}
