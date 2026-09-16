// 量化仓位参数选择器（plan 4.2.1/4.4.1）：已发布策略版本 + 组合（含 version 快照校验）。

import type { UseFormReturn } from 'react-hook-form';

import { Label } from '../../../../shared/ui/label';
import { useListQuantStrategies } from '../strategies/queries';
import { usePortfoliosQuery } from '../../../watchlist/queries';
import type { AnalysisTaskFormValues } from './AnalysisTaskForm';

interface QuantParamsPickerProps {
  form: UseFormReturn<AnalysisTaskFormValues>;
}

export function QuantParamsPicker({ form }: QuantParamsPickerProps) {
  const strategies = useListQuantStrategies();
  const portfolios = usePortfoliosQuery();

  const versionOptions = (strategies.data ?? []).flatMap((strategy) =>
    strategy.versions
      .filter((v) => v.status === 'PUBLISHED')
      .map((v) => ({ value: v.id, label: `${strategy.name} v${v.version_no}` })),
  );
  const portfolioOptions = (portfolios.data?.pages.flatMap((page) => page.items) ?? []).map((p) => ({
    id: p.id,
    name: p.name,
    version: p.version,
  }));

  const selectedVersion = form.watch('strategy_version_id');
  const selectedPortfolio = form.watch('portfolio_id');

  function onVersionChange(value: string) {
    form.setValue('strategy_version_id', value || undefined, { shouldValidate: true });
  }

  function onPortfolioChange(value: string) {
    form.setValue('portfolio_id', value || undefined, { shouldValidate: true });
    const target = portfolioOptions.find((p) => p.id === value);
    form.setValue('expected_portfolio_version', target ? target.version : undefined, { shouldValidate: true });
  }

  return (
    <div className="flex flex-col gap-1.5 rounded-md border p-2" aria-label="量化仓位参数">
      <Label htmlFor="strategy_version">已发布策略版本</Label>
      <select
        id="strategy_version"
        className="rounded-md border p-2 text-sm"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg)' }}
        value={selectedVersion ?? ''}
        onChange={(e) => onVersionChange(e.target.value)}
      >
        <option value="">选择策略版本…</option>
        {versionOptions.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {form.formState.errors.strategy_version_id && (
        <p className="text-xs text-red-400">{form.formState.errors.strategy_version_id.message}</p>
      )}

      <div className="flex items-center justify-between">
        <Label htmlFor="portfolio">组合（任务冻结提交时快照，行情执行时实时读取）</Label>
        {portfolios.hasNextPage && (
          <button
            type="button"
            className="text-xs underline"
            disabled={portfolios.isFetchingNextPage}
            onClick={() => void portfolios.fetchNextPage()}
          >
            {portfolios.isFetchingNextPage ? '加载中…' : '加载更多组合'}
          </button>
        )}
      </div>
      <select
        id="portfolio"
        className="rounded-md border p-2 text-sm"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg)' }}
        value={selectedPortfolio ?? ''}
        onChange={(e) => onPortfolioChange(e.target.value)}
      >
        <option value="">选择组合…</option>
        {portfolioOptions.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}（v{p.version}）
          </option>
        ))}
      </select>
      {portfolios.isError && (
        <p className="text-xs text-red-400">组合列表加载失败，请刷新页面重试</p>
      )}
      {form.formState.errors.portfolio_id && (
        <p className="text-xs text-red-400">{form.formState.errors.portfolio_id.message}</p>
      )}
      {selectedPortfolio && (
        <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          提交时校验组合版本 v{form.watch('expected_portfolio_version')}；冲突请重选。
        </p>
      )}
    </div>
  );
}
