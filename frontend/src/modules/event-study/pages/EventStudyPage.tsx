import { useSearchParams } from 'react-router';
import { Button } from '../../../shared/ui/button';
import { PredictionTab } from './PredictionTab';
import { ReviewTab } from './review/ReviewTab';

type EventStudyTab = 'predict' | 'review';

// 事件研究 hub：一级 tab 下的两个子 Tab（影响预测 / 事件审核），tab 状态走 URL ?tab=。
// 切换时保留其余参数（如宏观卡片跳转的 ?event_id=），供预测 Tab 预填使用。
export default function EventStudyPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: EventStudyTab = searchParams.get('tab') === 'review' ? 'review' : 'predict';

  function switchTab(next: EventStudyTab) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === 'review') {
          params.set('tab', 'review');
        } else {
          params.delete('tab');
        }
        return params;
      },
      { replace: true },
    );
  }

  return (
    <main className="flex flex-col gap-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">事件研究</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            基于历史事件样本的影响预测，与爬虫事件的采集审核
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant={tab === 'predict' ? 'default' : 'outline'}
            onClick={() => switchTab('predict')}
          >
            影响预测
          </Button>
          <Button
            size="sm"
            variant={tab === 'review' ? 'default' : 'outline'}
            onClick={() => switchTab('review')}
          >
            事件审核
          </Button>
        </div>
      </header>

      {tab === 'review' ? <ReviewTab /> : <PredictionTab />}
    </main>
  );
}
