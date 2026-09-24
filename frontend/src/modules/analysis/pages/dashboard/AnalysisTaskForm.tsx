import { useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { CircleHelp } from 'lucide-react';
import { ApiError, toApiError } from '../../../../api/client';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { todayLocalDate } from '../../../../shared/format/dateTime';
import { Button } from '../../../../shared/ui/button';
import { Checkbox } from '../../../../shared/ui/checkbox';
import { Input } from '../../../../shared/ui/input';
import { Label } from '../../../../shared/ui/label';
import { LAYER_LABELS } from '../../shared/analysisLayers';
import { isStaleInputError, useCreateAnalysisTaskMutation, type CreateTaskVariables } from './queries';
import { QuantParamsPicker } from './QuantParamsPicker';

// 创建分析任务面板（产品决策 2026-09-06 v3）：新建分析恒为全市场调研——
// 无目标标的代码输入；分析层级默认全部不选、自由勾选（任意非空组合），
// 仓位独立可选。请求体恒为 MARKET_WIDE；后端契约已同步放开组合限制。
export type AnalysisLayer = 'market' | 'sector' | 'stock' | 'screening' | 'position';

const analysisLayerValues = ['market', 'sector', 'stock', 'screening', 'position'] as const;

export const analysisTaskFormSchema = z
  .object({
    requested_trade_date: z.string().min(1, '请选择请求交易日'),
    selected_layers: z.array(z.enum(analysisLayerValues)),
    strategy_version_id: z.string().optional(),
    portfolio_id: z.string().optional(),
    expected_portfolio_version: z.number().int().positive().optional(),
  })
  .superRefine((value, ctx) => {
    if (value.selected_layers.length === 0) {
      ctx.addIssue({ code: 'custom', path: ['selected_layers'], message: '请至少选择一个分析层级' });
    }
    const hasPosition = value.selected_layers.includes('position');
    if (hasPosition) {
      // 量化仓位层：策略/组合三字段必填（决策 11：position 独立成任务，不再要求 screening）
      if (!value.strategy_version_id) {
        ctx.addIssue({ code: 'custom', path: ['strategy_version_id'], message: '请选择已发布策略版本' });
      }
      if (!value.portfolio_id) {
        ctx.addIssue({ code: 'custom', path: ['portfolio_id'], message: '请选择组合' });
      }
      if (!value.expected_portfolio_version) {
        ctx.addIssue({ code: 'custom', path: ['expected_portfolio_version'], message: '组合版本缺失' });
      }
    } else if (value.strategy_version_id || value.portfolio_id || value.expected_portfolio_version) {
      ctx.addIssue({ code: 'custom', path: ['selected_layers'], message: '未选择仓位层时不能携带量化参数' });
    }
  });

export type AnalysisTaskFormValues = z.infer<typeof analysisTaskFormSchema>;

interface AnalysisTaskFormProps {
  onCancel: () => void;
}

export function AnalysisTaskForm({ onCancel }: AnalysisTaskFormProps) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialLayers = [...new Set((searchParams.get('layers') ?? '').split(','))].filter((layer): layer is AnalysisLayer => analysisLayerValues.includes(layer as AnalysisLayer));
  const mutation = useCreateAnalysisTaskMutation();
  // 同一意图复用 Idempotency-Key；任一输入改变后生成新 key（API 契约 §1.4）
  const idempotencyRef = useRef<{ key: string; snapshot: string } | null>(null);
  const lastSubmitRef = useRef<CreateTaskVariables | null>(null);

  const form = useForm<AnalysisTaskFormValues>({
    resolver: zodResolver(analysisTaskFormSchema),
    defaultValues: {
      requested_trade_date: todayLocalDate(),
      selected_layers: initialLayers,  // 默认什么都不选，由用户勾选（至少一个）
    },
  });

  const values = form.watch();
  const validity = analysisTaskFormSchema.safeParse(values);
  const disabledReason = validity.success ? null : validity.error.issues[0]?.message;
  const selectedLayers = form.watch('selected_layers');

  function toggleLayer(layer: AnalysisLayer, checked: boolean) {
    const next = new Set(selectedLayers);
    if (checked) {
      next.add(layer);
    } else {
      next.delete(layer);
      if (layer === 'position') {
        // 取消仓位层时清空量化三字段（避免「落库未生效」）
        form.setValue('strategy_version_id', undefined);
        form.setValue('portfolio_id', undefined);
        form.setValue('expected_portfolio_version', undefined);
      }
    }
    form.setValue('selected_layers', Array.from(next));
  }

  function currentIdempotencyKey(values: AnalysisTaskFormValues): string {
    const snapshot = JSON.stringify({
      requested_trade_date: values.requested_trade_date,
      selected_layers: values.selected_layers,
      strategy_version_id: values.strategy_version_id ?? null,
      portfolio_id: values.portfolio_id ?? null,
      expected_portfolio_version: values.expected_portfolio_version ?? null,
    });
    if (!idempotencyRef.current || idempotencyRef.current.snapshot !== snapshot) {
      idempotencyRef.current = { key: crypto.randomUUID(), snapshot };
    }
    return idempotencyRef.current.key;
  }

  function onSubmit(values: AnalysisTaskFormValues) {
    const idempotencyKey = currentIdempotencyKey(values);
    const variables: CreateTaskVariables = {
      request: {
        task_type: 'MARKET_WIDE',
        ticker: null,
        requested_trade_date: values.requested_trade_date,
        selected_layers: values.selected_layers,
        strategy_version_id: values.strategy_version_id ?? null,
        portfolio_id: values.portfolio_id ?? null,
        expected_portfolio_version: values.expected_portfolio_version ?? null,
      },
      idempotencyKey,
    };
    lastSubmitRef.current = variables;
    mutation.mutate(variables, {
      onSuccess: (result) => {
        // 创建成功从 envelope data.task_id 读取 ID，立即跳转任务详情
        navigate(`/ai/tasks/${result.task_id}`);
      },
    });
  }

  function retryLastSubmit() {
    if (lastSubmitRef.current) mutation.mutate(lastSubmitRef.current);
  }

  const submitError = mutation.error;

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4" aria-label="创建分析任务">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="requested_trade_date">请求交易日</Label>
        <Input id="requested_trade_date" type="date" {...form.register('requested_trade_date')} />
        {form.formState.errors.requested_trade_date && (
          <p className="text-xs text-red-400">{form.formState.errors.requested_trade_date.message}</p>
        )}
      </div>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-sm font-medium">分析层级（全市场调研，层级自由组合）</legend>
        {(['market', 'sector', 'stock', 'screening'] as AnalysisLayer[]).map((layer) => (
          <label key={layer} className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={selectedLayers.includes(layer)}
              onChange={(event) => toggleLayer(layer, event.target.checked)}
            />
            {LAYER_LABELS[layer]}
          </label>
        ))}
        <div className="flex items-center gap-2 text-sm">
          <label className="flex items-center gap-2">
            <Checkbox
              checked={selectedLayers.includes('position')}
              onChange={(event) => toggleLayer('position', event.target.checked)}
            />
            仓位
          </label>
          <span className="group relative inline-flex" aria-label="仓位说明">
            <CircleHelp className="size-3.5" style={{ color: 'var(--color-fg-muted)' }} aria-hidden />
            <span
              role="tooltip"
              className="pointer-events-none invisible absolute left-1/2 top-full z-10 mt-1.5 w-64 -translate-x-1/2 rounded-md border p-2 text-xs opacity-0 transition-opacity group-hover:visible group-hover:opacity-100"
              style={{
                borderColor: 'var(--color-border)',
                background: 'var(--color-surface)',
                color: 'var(--color-fg-muted)',
              }}
            >
              量化全市场扫描：选择已发布策略与组合，建议订单需人工确认、不自动下单
            </span>
          </span>
        </div>
        {selectedLayers.includes('position') && <QuantParamsPicker form={form} />}
        {form.formState.errors.selected_layers && (
          <p className="text-xs text-red-400">{form.formState.errors.selected_layers.message}</p>
        )}
      </fieldset>

      {submitError && (
        <ErrorState
          error={toApiError(submitError)}
          onRetry={
            submitError instanceof ApiError && submitError.retryable && !isStaleInputError(submitError)
              ? retryLastSubmit
              : undefined
          }
        />
      )}

      <div className="rounded-xl bg-[var(--color-accent-soft)] p-3 text-xs text-[var(--color-fg)]">
        {selectedLayers.length ? `已选：${selectedLayers.map(layer => LAYER_LABELS[layer]).join('、')}。` : '请先选择需要的分析层级。'}
        {selectedLayers.includes('position') && '仓位分析需要已发布策略与组合，建议订单需人工确认。'}
        {disabledReason && <p className="mt-1 text-[var(--color-fg-muted)]" id="analysis-submit-reason">{disabledReason}</p>}
      </div>
      <div className="flex justify-end gap-3">
        <Button type="button" variant="outline" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" aria-describedby={disabledReason ? "analysis-submit-reason" : undefined} disabled={mutation.isPending || !validity.success}>
          {mutation.isPending ? '提交中…' : '提交分析'}
        </Button>
      </div>
    </form>
  );
}
