import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EventDetailDialog } from './EventDetailDialog';
import type { PendingEventRowVM } from './mappers/toPendingEventRowVM';

function vm(overrides: Partial<PendingEventRowVM> = {}): PendingEventRowVM {
  return {
    draftId: 1,
    announcedAt: '2026-09-16T09:00:00+08:00',
    source: '财联社电报',
    title: '测试事件',
    content: '正文内容',
    sourceUrl: null,
    eventType: '宏观数据',
    eventSubtype: '',
    eventCondition: '',
    importance: 3,
    eventScope: 'market',
    affectedScopeRefs: '',
    expectedValue: '',
    actualValue: '',
    previousValue: '',
    action: 'skip',
    aiSuggestions: null,
    ...overrides,
  };
}

describe('EventDetailDialog', () => {
  it('渲染标题与原文', () => {
    render(<EventDetailDialog open vm={vm()} onClose={() => {}} />);
    expect(screen.getByText('#1 测试事件')).toBeInTheDocument();
    expect(screen.getByText('正文内容')).toBeInTheDocument();
  });

  it('unresolved_entities 非空时渲染警示（含格式提示）', () => {
    render(
      <EventDetailDialog
        open
        vm={vm({ aiSuggestions: { event_scope: 'sector', unresolved_entities: ['英伟达', '新概念X'] } })}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/未解析目标：英伟达、新概念X/)).toBeInTheDocument();
    expect(screen.getByText(/SW:801080 \/ CONCEPT:BK1753\.DC \/ stock:600519\.SH/)).toBeInTheDocument();
  });

  it('unresolved_entities 缺失时不渲染警示', () => {
    render(
      <EventDetailDialog open vm={vm({ aiSuggestions: { event_scope: 'sector' } })} onClose={() => {}} />,
    );
    expect(screen.queryByText(/未解析目标/)).not.toBeInTheDocument();
  });

  it('unresolved_entities 非数组（运行时守卫）不渲染警示', () => {
    render(
      <EventDetailDialog
        open
        vm={vm({ aiSuggestions: { event_scope: 'sector', unresolved_entities: '英伟达' } })}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByText(/未解析目标/)).not.toBeInTheDocument();
  });

  it('无 AI 建议时显示（无）', () => {
    render(<EventDetailDialog open vm={vm()} onClose={() => {}} />);
    expect(screen.getByText('（无）')).toBeInTheDocument();
  });
});
