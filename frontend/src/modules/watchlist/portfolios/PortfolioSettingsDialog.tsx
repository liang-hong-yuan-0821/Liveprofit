import { PercentField, percentDraft, percentValue } from '../../../shared/ui/PercentField';
// 组合账户设置（plan 4.2.1）：原子编辑名称与全部资金/风控字段，
// 409 冲突时重新拉取组合与持仓；量化生成订单前复核实时账户和资格。

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

import type { PortfolioDTO } from '../../../api/generated';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { Button } from '../../../shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../../../shared/ui/dialog';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import { useUpdatePortfolioMutation } from '../queries';

interface PortfolioSettingsDialogProps {
  portfolio: PortfolioDTO;
  trigger: React.ReactNode;
}

const PROFILE_CAPS = {
  CONSERVATIVE: { trade: .0025, open: .02, total: .50, single: .05, drawdown: .08, sectorRisk: .01, dailyRisk: .01 },
  BALANCED: { trade: .005, open: .04, total: .75, single: .08, drawdown: .15, sectorRisk: .02, dailyRisk: .02 },
  AGGRESSIVE: { trade: .0075, open: .06, total: .90, single: .10, drawdown: .25, sectorRisk: .03, dailyRisk: .03 },
} as const;

export function PortfolioSettingsDialog({ portfolio, trigger }: PortfolioSettingsDialogProps) {
  const [open, setOpen] = useState(false);
  const [original, setOriginal] = useState(portfolio);
  const [groups, setGroups] = useState<Set<string>>(new Set(['basic']));
  const [conflict, setConflict] = useState(false);
  const stale = conflict || portfolio.version !== original.version;
  const [name, setName] = useState(portfolio.name);
  const [totalAssets, setTotalAssets] = useState(String(portfolio.total_assets));
  const [availableCash, setAvailableCash] = useState(String(portfolio.available_cash));
  const [riskPct, setRiskPct] = useState(percentDraft(portfolio.risk_per_trade_pct));
  const [rr, setRr] = useState(String(portfolio.min_risk_reward_ratio));
  const [totalPct, setTotalPct] = useState(percentDraft(portfolio.max_total_position_pct));
  const [singlePct, setSinglePct] = useState(percentDraft(portfolio.max_single_stock_pct));
  const [sectorPct, setSectorPct] = useState(percentDraft(portfolio.max_sector_pct));
  const [portfolioRiskPct, setPortfolioRiskPct] = useState(percentDraft(portfolio.max_portfolio_open_risk_pct));
  const [sectorRiskPct, setSectorRiskPct] = useState(percentDraft(portfolio.max_sector_open_risk_pct));
  const [dailyRiskPct, setDailyRiskPct] = useState(percentDraft(portfolio.max_daily_new_risk_pct));
  const [drawdownPct, setDrawdownPct] = useState(percentDraft(portfolio.max_drawdown_pct));
  const [dailyLossPct, setDailyLossPct] = useState(percentDraft(portfolio.max_daily_loss_pct));
  const [netAssetValue, setNetAssetValue] = useState(portfolio.net_asset_value?.toString() ?? '');
  const [peakNetAssetValue, setPeakNetAssetValue] = useState(portfolio.peak_net_asset_value?.toString() ?? '');
  const [dayStartNetAssetValue, setDayStartNetAssetValue] = useState(portfolio.day_start_net_asset_value?.toString() ?? '');
  const [riskFactsAsOf, setRiskFactsAsOf] = useState(portfolio.risk_facts_as_of ?? '');
  const [riskProfile, setRiskProfile] = useState<PortfolioDTO["risk_profile"]>(portfolio.risk_profile ?? null);
  const mutation = useUpdatePortfolioMutation();
  const [formError, setFormError] = useState<string | null>(null);

  const contentRef = useRef<HTMLDivElement>(null);
  const [focusRequest, setFocusRequest] = useState<{ label: string } | null>(null);
  useEffect(() => {
    if (!focusRequest) return;
    const input = Array.from(contentRef.current?.querySelectorAll('input') ?? []).find(node =>
      Array.from(node.labels ?? []).some(label => label.textContent?.startsWith(focusRequest.label)));
    input?.focus();
  }, [focusRequest]);
  function showError(message: string, label?: string) {
    setFormError(message); setGroups(new Set(['basic', 'position', 'risk', 'facts']));
    if (label) setFocusRequest({ label });
  }
  function submit() {
    setFormError(null);
    if (stale) { showError('账户已更新，请重新加载；草稿已保留。'); return; }
    if (!totalAssets.trim() || !availableCash.trim() || !rr.trim() || Number(totalAssets) <= 0 || Number(rr) <= 0) { showError('请填写总资产、可用现金与最低盈亏比；总资产与盈亏比必须大于 0', !totalAssets.trim() || Number(totalAssets) <= 0 ? '总资产' : !availableCash.trim() ? '可用现金' : '最低盈亏比'); return; }
    const fields: Array<[string, number]> = [
      ['总资产', Number(totalAssets)],
      ['可用现金', Number(availableCash)],
      ['单笔风险比例', percentValue(riskPct, original.risk_per_trade_pct)],
      ['最低盈亏比', Number(rr)],
      ['总仓位上限', percentValue(totalPct, original.max_total_position_pct)],
      ['单票市值上限', percentValue(singlePct, original.max_single_stock_pct)],
      ['行业上限', percentValue(sectorPct, original.max_sector_pct)],
      ['组合开放风险上限', percentValue(portfolioRiskPct, original.max_portfolio_open_risk_pct)],
      ['行业开放风险上限', percentValue(sectorRiskPct, original.max_sector_open_risk_pct)],
      ['单日新增风险上限', percentValue(dailyRiskPct, original.max_daily_new_risk_pct)],
      ['最大回撤熔断', percentValue(drawdownPct, original.max_drawdown_pct)],
      ['单日损失熔断', percentValue(dailyLossPct, original.max_daily_loss_pct)],
    ];
    for (const [label, value] of fields) {
      if (!Number.isFinite(value)) {
        showError(`${label}必须是有效数字`, label);
        return;
      }
    }
    const assets = Number(totalAssets);
    const cash = Number(availableCash);
    const total = percentValue(totalPct, original.max_total_position_pct);
    if (cash < 0 || cash > assets) {
      showError('可用现金必须在 [0, 总资产] 区间', '可用现金');
      return;
    }
    for (const [label, value] of [
      ['单笔风险比例', percentValue(riskPct, original.risk_per_trade_pct)],
      ['总仓位上限', total],
      ['单票市值上限', percentValue(singlePct, original.max_single_stock_pct)],
      ['行业上限', percentValue(sectorPct, original.max_sector_pct)],
      ['组合开放风险上限', percentValue(portfolioRiskPct, original.max_portfolio_open_risk_pct)],
      ['行业开放风险上限', percentValue(sectorRiskPct, original.max_sector_open_risk_pct)],
      ['单日新增风险上限', percentValue(dailyRiskPct, original.max_daily_new_risk_pct)],
      ['最大回撤熔断', percentValue(drawdownPct, original.max_drawdown_pct)],
      ['单日损失熔断', percentValue(dailyLossPct, original.max_daily_loss_pct)],
    ] as Array<[string, number]>) {
      if (value <= 0 || value > 1) {
        showError(`${label}必须大于 0% 且不超过 100%`, label);
        return;
      }
    }
    if (percentValue(singlePct, original.max_single_stock_pct) > total || percentValue(sectorPct, original.max_sector_pct) > total) {
      showError('单票/行业上限必须 ≤ 总仓位上限', percentValue(singlePct, original.max_single_stock_pct) > total ? '单票市值上限' : '行业上限');
      return;
    }
    if (percentValue(sectorRiskPct, original.max_sector_open_risk_pct) > percentValue(portfolioRiskPct, original.max_portfolio_open_risk_pct)) {
      showError('行业开放风险上限必须 ≤ 组合开放风险上限', '行业开放风险上限');
      return;
    }
    if (riskProfile) {
      const cap = PROFILE_CAPS[riskProfile];
      for (const [label, value, limit] of [
        ['单笔风险比例', percentValue(riskPct, original.risk_per_trade_pct), cap.trade],
        ['组合开放风险上限', percentValue(portfolioRiskPct, original.max_portfolio_open_risk_pct), cap.open],
        ['总仓位上限', total, cap.total],
        ['单票市值上限', percentValue(singlePct, original.max_single_stock_pct), cap.single],
        ['最大回撤熔断', percentValue(drawdownPct, original.max_drawdown_pct), cap.drawdown],
        ['行业开放风险上限', percentValue(sectorRiskPct, original.max_sector_open_risk_pct), cap.sectorRisk],
        ['单日新增风险上限', percentValue(dailyRiskPct, original.max_daily_new_risk_pct), cap.dailyRisk],
      ] as Array<[string, number, number]>) {
        if (value > limit) {
          showError(`${label}超过${riskProfile === 'CONSERVATIVE' ? '保守' : riskProfile === 'BALANCED' ? '均衡' : '进取'}档上限 ${(limit * 100).toFixed(2)}%`, label);
          return;
        }
      }
    }
    const facts = [netAssetValue, peakNetAssetValue, dayStartNetAssetValue, riskFactsAsOf];
    const anyFact = facts.some((value) => value.trim() !== '');
    const allFacts = facts.every((value) => value.trim() !== '');
    if (anyFact && !allFacts) {
      showError('净值、峰值净值、日初净值和事实日期必须同时填写', ['当前净值', '历史峰值净值', '日初净值', '风险事实日期'][facts.findIndex(value => !value.trim())]);
      return;
    }
    if (allFacts && [netAssetValue, peakNetAssetValue, dayStartNetAssetValue].some((value) => !Number.isFinite(Number(value)) || Number(value) <= 0)) {
      showError('三项净值必须为大于 0 的有效数字', ['当前净值', '历史峰值净值', '日初净值'][facts.slice(0, 3).findIndex(value => !Number.isFinite(Number(value)) || Number(value) <= 0)]);
      return;
    }
    if (allFacts && Number(peakNetAssetValue) < Number(netAssetValue)) {
      showError('峰值净值必须 ≥ 当前净值', '历史峰值净值');
      return;
    }
    mutation.mutate(
      {
        portfolioId: portfolio.id,
        name: name.trim(),
        totalAssets: Number(totalAssets),
        availableCash: Number(availableCash),
        riskPerTradePct: percentValue(riskPct, original.risk_per_trade_pct),
        minRiskRewardRatio: Number(rr),
        maxTotalPositionPct: percentValue(totalPct, original.max_total_position_pct),
        maxSingleStockPct: percentValue(singlePct, original.max_single_stock_pct),
        maxSectorPct: percentValue(sectorPct, original.max_sector_pct),
        maxPortfolioOpenRiskPct: percentValue(portfolioRiskPct, original.max_portfolio_open_risk_pct),
        maxSectorOpenRiskPct: percentValue(sectorRiskPct, original.max_sector_open_risk_pct),
        maxDailyNewRiskPct: percentValue(dailyRiskPct, original.max_daily_new_risk_pct),
        maxDrawdownPct: percentValue(drawdownPct, original.max_drawdown_pct),
        maxDailyLossPct: percentValue(dailyLossPct, original.max_daily_loss_pct),
        netAssetValue: allFacts ? Number(netAssetValue) : null,
        peakNetAssetValue: allFacts ? Number(peakNetAssetValue) : null,
        dayStartNetAssetValue: allFacts ? Number(dayStartNetAssetValue) : null,
        riskFactsAsOf: allFacts ? riskFactsAsOf : null,
        riskProfile,
        expectedVersion: original.version,
      },
      { onSuccess: () => setOpen(false), onError: error => { if (toApiError(error).status === 409) setConflict(true); } },
    );
  }

  function reseed() {
    setGroups(new Set(['basic']));
    setOriginal(portfolio); setConflict(false); setFormError(null); mutation.reset();
    setName(portfolio.name);
    setTotalAssets(String(portfolio.total_assets));
    setAvailableCash(String(portfolio.available_cash));
    setRiskPct(percentDraft(portfolio.risk_per_trade_pct));
    setRr(String(portfolio.min_risk_reward_ratio));
    setTotalPct(percentDraft(portfolio.max_total_position_pct));
    setSinglePct(percentDraft(portfolio.max_single_stock_pct));
    setSectorPct(percentDraft(portfolio.max_sector_pct));
    setPortfolioRiskPct(percentDraft(portfolio.max_portfolio_open_risk_pct));
    setSectorRiskPct(percentDraft(portfolio.max_sector_open_risk_pct));
    setDailyRiskPct(percentDraft(portfolio.max_daily_new_risk_pct));
    setDrawdownPct(percentDraft(portfolio.max_drawdown_pct));
    setDailyLossPct(percentDraft(portfolio.max_daily_loss_pct));
    setNetAssetValue(portfolio.net_asset_value?.toString() ?? '');
    setPeakNetAssetValue(portfolio.peak_net_asset_value?.toString() ?? '');
    setDayStartNetAssetValue(portfolio.day_start_net_asset_value?.toString() ?? '');
    setRiskFactsAsOf(portfolio.risk_facts_as_of ?? '');
    setRiskProfile(portfolio.risk_profile ?? null);
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (next) reseed(); }}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent ref={contentRef} className="max-w-lg">
        <DialogHeader className="sticky -top-6 z-10 -mx-6 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-6 py-4">
          <DialogTitle>组合账户设置（{portfolio.name}）</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <SettingsGroup title="账户基础" open={groups.has('basic')} onToggle={() => setGroups(current => { const next = new Set(current); next.has('basic') ? next.delete('basic') : next.add('basic'); return next; })}>
          <label className="flex flex-col gap-1 text-sm">策略资格风险档位
            <select className="rounded border bg-[var(--color-bg)] p-2" value={riskProfile ?? ''} onChange={event => setRiskProfile((event.target.value || null) as PortfolioDTO['risk_profile'])}>
              <option value="">未选择（暂停开仓、加仓）</option>
              <option value="CONSERVATIVE">保守</option>
              <option value="BALANCED">均衡</option>
              <option value="AGGRESSIVE">进取</option>
            </select>
            <span className="text-xs text-[var(--color-fg-muted)]">匹配该档位下已验证的策略资格；各风险参数可更保守，超过档位上限时不能保存。</span>
          </label>
          <Field label="名称" value={name} onChange={setName} />
          <Field label="总资产（元）" value={totalAssets} onChange={setTotalAssets} />
          <Field label="可用现金（元）" value={availableCash} onChange={setAvailableCash} />
          </SettingsGroup>
          <SettingsGroup title="仓位上限" open={groups.has('position')} onToggle={() => setGroups(current => { const next = new Set(current); next.has('position') ? next.delete('position') : next.add('position'); return next; })}>
          <PercentField label="总仓位上限" value={totalPct} onChange={setTotalPct} />
          <PercentField label="单票市值上限" value={singlePct} onChange={setSinglePct} />
          <PercentField label="行业上限" value={sectorPct} onChange={setSectorPct} />
          </SettingsGroup>
          <SettingsGroup title="风险限制" open={groups.has('risk')} onToggle={() => setGroups(current => { const next = new Set(current); next.has('risk') ? next.delete('risk') : next.add('risk'); return next; })}>
          <PercentField label="单笔风险比例" value={riskPct} onChange={setRiskPct} />
          <Field label="最低盈亏比" value={rr} onChange={setRr} />
          <PercentField label="组合开放风险上限" value={portfolioRiskPct} onChange={setPortfolioRiskPct} />
          <PercentField label="行业开放风险上限" value={sectorRiskPct} onChange={setSectorRiskPct} />
          <PercentField label="单日新增风险上限" value={dailyRiskPct} onChange={setDailyRiskPct} />
          <PercentField label="最大回撤熔断" value={drawdownPct} onChange={setDrawdownPct} />
          <PercentField label="单日损失熔断" value={dailyLossPct} onChange={setDailyLossPct} />
          </SettingsGroup>
          <SettingsGroup title="净值事实（整组可选）" open={groups.has('facts')} onToggle={() => setGroups(current => { const next = new Set(current); next.has('facts') ? next.delete('facts') : next.add('facts'); return next; })}>
          <Field label="当前净值（可空）" value={netAssetValue} onChange={setNetAssetValue} />
          <Field label="历史峰值净值（可空）" value={peakNetAssetValue} onChange={setPeakNetAssetValue} />
          <Field label="日初净值（可空）" value={dayStartNetAssetValue} onChange={setDayStartNetAssetValue} />
            <label className="flex flex-col gap-1 text-sm">风险事实日期（可空）<Input type="date" value={riskFactsAsOf} onChange={event => setRiskFactsAsOf(event.target.value)} /></label>
          </SettingsGroup>
        </div>
        <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          量化任务保存提交时快照，生成建议订单前会复核最新账户与策略资格。校验：现金 ∈ [0, 总资产]、
          各比例大于 0% 且不超过 100%、单票 ≤ 总仓位、行业 ≤ 总仓位。
        </p>
        {stale && <p role="alert" className="text-xs text-amber-500">账户已有更新，草稿已保留。<button type="button" onClick={reseed} className="ml-2 underline">放弃草稿并加载最新设置</button></p>}
        {formError && <p className="text-xs text-red-400" role="alert">{formError}</p>}
        {mutation.isError && <ErrorState error={toApiError(mutation.error)} />}
        <div className="sticky -bottom-6 -mx-6 flex justify-end gap-2 border-t border-[var(--color-border)] bg-[var(--color-bg)] px-6 py-4">
          <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
          <Button disabled={mutation.isPending || !name.trim() || stale} onClick={submit}>保存</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function SettingsGroup({ title, open, onToggle, children }: { title: string; open: boolean; onToggle: () => void; children: ReactNode }) {
  const id = useId();
  return <section className="rounded-xl border border-[var(--color-border)] p-3"><button type="button" aria-expanded={open} aria-controls={id} onClick={onToggle} className="flex w-full items-center justify-between text-sm font-semibold text-[var(--color-accent-bright)]">{title}<span aria-hidden="true">{open ? '−' : '+'}</span></button><div id={id} hidden={!open} className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div></section>;
}
