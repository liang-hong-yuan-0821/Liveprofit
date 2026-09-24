import { useSearchParams } from 'react-router';
import { SectionTabs } from '../../../shared/ui/SectionTabs';
import { EventForecastTab } from './EventForecastTab';
import { ReviewTab } from './review/ReviewTab';

type EventStudyTab = 'events' | 'review';

// 事件研究 hub：一级 tab 下的两个子 Tab（影响预测 / 事件审核），tab 状态走 URL ?tab=。
// 切换时保留其余参数（如宏观卡片跳转的 ?event_id=），供预测 Tab 预填使用。
export default function EventStudyPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: EventStudyTab = searchParams.get('tab') === 'review' ? 'review' : 'events';

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
            按每日分析批次查看自动采集的事件、期限判断、事实阶段与证据；正常新闻无需手工录入
          </p>
        </div>
      </header>
      <SectionTabs label="事件研究工作区" value={tab} onChange={switchTab} items={[{ value: 'events', label: '每日事件预测' }, { value: 'review', label: '争议与事件审核' }]} />

      {tab === 'review' ? <ReviewTab /> : <EventForecastTab />}
    </main>
  );
}
