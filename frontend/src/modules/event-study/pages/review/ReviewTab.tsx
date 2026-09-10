import { useState } from 'react';
import { Button } from '../../../../shared/ui/button';
import { ImpactConfirmTab } from './ImpactConfirmTab';
import { PendingEventsTab } from './PendingEventsTab';

type ReviewSubTab = 'pending' | 'impacts';

// 事件研究·审核（平台集成版，行为对齐 Streamlit review_app 两个 Tab）。
export function ReviewTab() {
  const [tab, setTab] = useState<ReviewSubTab>('pending');

  return (
    <section className="flex flex-col gap-4">
      <div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant={tab === 'pending' ? 'default' : 'outline'} onClick={() => setTab('pending')}>
            待审核事件
          </Button>
          <Button size="sm" variant={tab === 'impacts' ? 'default' : 'outline'} onClick={() => setTab('impacts')}>
            影响结果确认
          </Button>
        </div>
        <p className="mt-2 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          审核爬虫采集的事件草稿，确认事件分类与关键条件；通过后自动计算影响结果，勾选确认落表
        </p>
      </div>

      {tab === 'pending' ? <PendingEventsTab /> : <ImpactConfirmTab />}
    </section>
  );
}
