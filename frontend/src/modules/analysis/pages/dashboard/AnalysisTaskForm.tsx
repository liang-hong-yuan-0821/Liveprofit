import { useRef } from 'react';
import { useNavigate } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { ApiError, toApiError } from '../../../../api/client';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { todayLocalDate } from '../../../../shared/format/dateTime';
import { Button } from '../../../../shared/ui/button';
import { Checkbox } from '../../../../shared/ui/checkbox';
import { Input } from '../../../../shared/ui/input';
import { Label } from '../../../../shared/ui/label';
import { useCreateAnalysisTaskMutation, type CreateTaskVariables } from './queries';

// 创建分析任务面板（产品决策 2026-09-06 v3）：新建分析恒为全市场调研——
// 无目标标的代码输入；市场/板块/筛选三个层级自由勾选（任意非空组合），
// 仓位可选（随筛选联动）。请求体恒为 MARKET_WIDE；后端契约已同步放开组合限制。
export type AnalysisLayer = 'market' | 'sector' | 'stock' | 'screening' | 'position';

const analysisLayerValues = ['market', 'sector', 'stock', 'screening', 'position'] as const;

export const analysisTaskFormSchema = z
  .object({
    requested_trade_date: z.string().min(1, '请选择请求交易日'),
    selected_layers: z.array(z.enum(analysisLayerValues)),
  })
  .superRefine((value, ctx) => {
    if (value.selected_layers.length === 0) {
      ctx.addIssue({ code: 'custom', path: ['selected_layers'], message: '请至少选择一个分析层级' });
    }
  });

export type AnalysisTaskFormValues = z.infer<typeof analysisTaskFormSchema>;

export const LAYER_LABELS: Record<AnalysisLayer, string> = {
  market: '市场',
  sector: '板块',
  stock: '个股',
  screening: '筛选',
  position: '仓位',
};

export const DEFAULT_LAYERS: AnalysisLayer[] = ['market', 'sector', 'screening'];

interface AnalysisTaskFormProps {
  onCancel: () => void;
}

export function AnalysisTaskForm({ onCancel }: AnalysisTaskFormProps) {
  const navigate = useNavigate();
  const mutation = useCreateAnalysisTaskMutation();
  // 同一意图复用 Idempotency-Key；任一输入改变后生成新 key（API 契约 §1.4）
  const idempotencyRef = useRef<{ key: string; snapshot: string } | null>(null);
  const lastSubmitRef = useRef<CreateTaskVariables | null>(null);

  const form = useForm<AnalysisTaskFormValues>({
    resolver: zodResolver(analysisTaskFormSchema),
    defaultValues: {
      requested_trade_date: todayLocalDate(),
      selected_layers: [...DEFAULT_LAYERS],
    },
  });

  const selectedLayers = form.watch('selected_layers');
  const hasScreening = selectedLayers.includes('screening');

  function toggleLayer(layer: AnalysisLayer, checked: boolean) {
    const next = new Set(selectedLayers);
    if (checked) {
      next.add(layer);
    } else {
      next.delete(layer);
      // 仓位仅可随筛选出现：取消筛选时联动取消仓位
      if (layer === 'screening') next.delete('position');
    }
    form.setValue('selected_layers', Array.from(next));
  }

  function currentIdempotencyKey(values: AnalysisTaskFormValues): string {
    const snapshot = JSON.stringify({
      requested_trade_date: values.requested_trade_date,
      selected_layers: values.selected_layers,
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
        {(['market', 'sector', 'screening'] as AnalysisLayer[]).map((layer) => (
          <label key={layer} className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={selectedLayers.includes(layer)}
              onChange={(event) => toggleLayer(layer, event.target.checked)}
            />
            {LAYER_LABELS[layer]}
          </label>
        ))}
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={selectedLayers.includes('position')}
            disabled={!hasScreening}
            onChange={(event) => toggleLayer('position', event.target.checked)}
          />
          仓位
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>（可选，需同时选择筛选）</span>
        </label>
        {form.formState.errors.selected_layers && (
          <p className="text-xs text-red-400">{form.formState.errors.selected_layers.message}</p>
        )}
      </fieldset>

      {submitError && (
        <ErrorState
          error={toApiError(submitError)}
          onRetry={submitError instanceof ApiError && submitError.retryable ? retryLastSubmit : undefined}
        />
      )}

      <div className="flex justify-end gap-3">
        <Button type="button" variant="outline" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? '提交中…' : '提交分析'}
        </Button>
      </div>
    </form>
  );
}
