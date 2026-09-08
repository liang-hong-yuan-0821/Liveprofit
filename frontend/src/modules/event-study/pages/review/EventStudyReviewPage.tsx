import { useState } from 'react';
import { Button } from '../../../../shared/ui/button';
import { ImpactConfirmTab } from './ImpactConfirmTab';
import { PendingEventsTab } from './PendingEventsTab';

type ReviewTab = 'pending' | 'impacts';

// 事件研究审核页（平台集成版，行为对齐 Streamlit review_app 两个 Tab）。
export default function EventStudyReviewPage() {
  const [tab, setTab] = useState<ReviewTab>('pending');

  return (
    <main className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">事件研究 — 审核</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            审核爬虫采集的事件草稿，确认事件分类与关键条件；通过后自动计算影响结果，勾选确认落表
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant={tab === 'pending' ? 'default' : 'outline'} onClick={() => setTab('pending')}>
            待审核事件
          </Button>
          <Button size="sm" variant={tab === 'impacts' ? 'default' : 'outline'} onClick={() => setTab('impacts')}>
            影响结果确认
          </Button>
        </div>
      </header>

      {tab === 'pending' ? <PendingEventsTab /> : <ImpactConfirmTab />}
    </main>
  );
}
