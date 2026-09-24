import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toIndexQuote } from '../../modules/market/pages/mappers/toIndexQuote';
import { normalizeEventText } from '../../modules/event-study/pages/review/normalizeEventText';
import { percentDraft, percentValue } from './PercentField';
import { AssetCombobox } from './AssetCombobox';
import { MarkdownView } from './markdown';
const bar = (day: number, close: number) => ({ timestamp: `2026-09-${String(day).padStart(2, '0')}`, open: close, high: close, low: close, close, volume: null });
describe('UX data boundaries', () => {
  it('sorts a copy, ignores invalid closes and never invents a percent', () => {
    const bars = [bar(3, 110), bar(1, 100), bar(2, NaN)];
    expect(toIndexQuote(bars).changePct).toBeCloseTo(10);
    expect(bars[0].close).toBe(110);
    for (const input of [[], [bar(1, 1)], [bar(1, 0), bar(2, 1)], [bar(1, -1), bar(2, 1)]]) expect(toIndexQuote(input).changePct).toBeNull();
  });
  it('percent drafts divide once and unchanged high precision values roundtrip exactly', () => {
    expect(percentDraft(.01)).toBe('1'); expect(percentValue('2.5', .01)).toBe(.025);
    const original = .123456789012345; expect(percentValue(percentDraft(original), original)).toBe(original);
    expect(Number.isNaN(percentValue('', .1))).toBe(true);
  });
  it('normalizes known event tags and decodes only one entity layer', () => {
    expect(normalizeEventText('<b>标题</b><p>A &amp; B</p>&amp;lt;script&amp;gt;')).toBe('标题\nA & B\n&lt;script&gt;');
    expect(normalizeEventText('<script>alert(1)</script>')).toContain('<script>');
  });
  it('review markdown cannot create a remote image', () => {
    const { container } = render(<MarkdownView content="![外部图](https://example.com/private.png)" imagePolicy="text" />);
    expect(container.querySelector('img')).toBeNull(); expect(screen.getByText('[图片：外部图]')).toBeInTheDocument();
  });
  it('asset keyboard selection submits the code; subsequent edits clear the old selection', async () => {
    const user = userEvent.setup(); const change = vi.fn();
    render(<AssetCombobox id="asset" value="" assets={[{ ticker: '000001.SZ', name: '平安银行', market: 'CN' }]} onChange={change} />);
    const input = screen.getByRole('combobox'); await user.type(input, '平安'); await user.keyboard('{ArrowDown}{Enter}');
    expect(change).toHaveBeenLastCalledWith('000001.SZ');
    await user.clear(input); await user.type(input, '未知代码'); expect(change).toHaveBeenLastCalledWith('未知代码');
    expect(screen.queryByText(/已选择/)).not.toBeInTheDocument();
  });
});
