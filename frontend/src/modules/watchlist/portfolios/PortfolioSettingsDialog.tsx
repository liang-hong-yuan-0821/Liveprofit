// 组合账户设置（plan 4.2.1）：原子编辑名称与全部资金/风控字段，
// 409 冲突时重新拉取组合与持仓；任务仅读取提交时快照。

import { useState } from 'react';

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

export function PortfolioSettingsDialog({ portfolio, trigger }: PortfolioSettingsDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(portfolio.name);
  const [totalAssets, setTotalAssets] = useState(String(portfolio.total_assets));
  const [availableCash, setAvailableCash] = useState(String(portfolio.available_cash));
  const [riskPct, setRiskPct] = useState(String(portfolio.risk_per_trade_pct));
  const [rr, setRr] = useState(String(portfolio.min_risk_reward_ratio));
  const [totalPct, setTotalPct] = useState(String(portfolio.max_total_position_pct));
  const [singlePct, setSinglePct] = useState(String(portfolio.max_single_stock_pct));
  const [sectorPct, setSectorPct] = useState(String(portfolio.max_sector_pct));
  const mutation = useUpdatePortfolioMutation();
  const [formError, setFormError] = useState<string | null>(null);

  function submit() {
    setFormError(null);
    const fields: Array<[string, number]> = [
      ['总资产', Number(totalAssets)],
      ['可用现金', Number(availableCash)],
      ['单笔风险比例', Number(riskPct)],
      ['最低盈亏比', Number(rr)],
      ['总仓位上限', Number(totalPct)],
      ['单票市值上限', Number(singlePct)],
      ['行业上限', Number(sectorPct)],
    ];
    for (const [label, value] of fields) {
      if (!Number.isFinite(value)) {
        setFormError(`${label}必须是有效数字`);
        return;
      }
    }
    const assets = Number(totalAssets);
    const cash = Number(availableCash);
    const total = Number(totalPct);
    if (cash < 0 || cash > assets) {
      setFormError('可用现金必须在 [0, 总资产] 区间');
      return;
    }
    for (const [label, value] of [
      ['单笔风险比例', Number(riskPct)],
      ['总仓位上限', total],
      ['单票市值上限', Number(singlePct)],
      ['行业上限', Number(sectorPct)],
    ] as Array<[string, number]>) {
      if (value <= 0 || value > 1) {
        setFormError(`${label}必须在 (0,1] 区间`);
        return;
      }
    }
    if (Number(singlePct) > total || Number(sectorPct) > total) {
      setFormError('单票/行业上限必须 ≤ 总仓位上限');
      return;
    }
    mutation.mutate(
      {
        portfolioId: portfolio.id,
        name: name.trim(),
        totalAssets: Number(totalAssets),
        availableCash: Number(availableCash),
        riskPerTradePct: Number(riskPct),
        minRiskRewardRatio: Number(rr),
        maxTotalPositionPct: Number(totalPct),
        maxSingleStockPct: Number(singlePct),
        maxSectorPct: Number(sectorPct),
        expectedVersion: portfolio.version,
      },
      { onSuccess: () => setOpen(false) },
    );
  }

  function reseed() {
    setName(portfolio.name);
    setTotalAssets(String(portfolio.total_assets));
    setAvailableCash(String(portfolio.available_cash));
    setRiskPct(String(portfolio.risk_per_trade_pct));
    setRr(String(portfolio.min_risk_reward_ratio));
    setTotalPct(String(portfolio.max_total_position_pct));
    setSinglePct(String(portfolio.max_single_stock_pct));
    setSectorPct(String(portfolio.max_sector_pct));
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (next) reseed(); }}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>组合账户设置（{portfolio.name}）</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <Field label="名称" value={name} onChange={setName} />
          <Field label="总资产（元）" value={totalAssets} onChange={setTotalAssets} />
          <Field label="可用现金（元）" value={availableCash} onChange={setAvailableCash} />
          <Field label="单笔风险比例（0-1）" value={riskPct} onChange={setRiskPct} />
          <Field label="最低盈亏比" value={rr} onChange={setRr} />
          <Field label="总仓位上限（0-1）" value={totalPct} onChange={setTotalPct} />
          <Field label="单票市值上限（0-1）" value={singlePct} onChange={setSinglePct} />
          <Field label="行业上限（0-1）" value={sectorPct} onChange={setSectorPct} />
        </div>
        <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          任务仅读取提交时的组合快照；修改账户参数不影响已提交任务。校验：现金 ∈ [0, 总资产]、
          各比例 ∈ (0,1]、单票 ≤ 总仓位、行业 ≤ 总仓位。
        </p>
        {formError && <p className="text-xs text-red-400" role="alert">{formError}</p>}
        {mutation.isError && <ErrorState error={toApiError(mutation.error)} />}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
          <Button disabled={mutation.isPending || !name.trim()} onClick={submit}>保存</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
