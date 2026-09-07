import { useRef } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { UseMutationResult } from '@tanstack/react-query';
import { toApiError } from '../../../api/client';
import type { PredictionData, PredictionRequest } from '../../../api/generated';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { Button } from '../../../shared/ui/button';
import { Checkbox } from '../../../shared/ui/checkbox';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';

export type PredictionMutation = UseMutationResult<PredictionData, unknown, PredictionRequest>;

// 预测表单：事件文本必填 1..20000；目标资产必填（可搜索下拉来自
// /api/v1/event-studies/assets，允许自由输入，不硬编码名单）；窗口仅允许契约枚举；
// 提交中防重；503/504 保留输入仅手动重试，不自动重试。
const predictionSchema = z.object({
  event_text: z.string().trim().min(1, '请输入事件文本').max(20000, '事件文本不能超过 20000 字符'),
  asset_ticker: z.string().trim().min(1, '请输入目标资产代码'),
  window_type: z.enum(['pre_event_5d', 'event_day', 'post_event_5d']),
  event_type: z.string().optional(),
  event_subtype: z.string().optional(),
  event_condition: z.string().optional(),
  save: z.boolean(),
});

export type PredictionFormValues = z.infer<typeof predictionSchema>;

export interface PredictionPrefill {
  eventText?: string;
  eventType?: string;
  assetTicker?: string;
}

interface PredictionFormProps {
  assets: { ticker: string; name: string; market: string }[];
  prefill: PredictionPrefill;
  /** mutation 由页面持有：表单与结果面板共享同一实例 */
  mutation: PredictionMutation;
}

export function PredictionForm({ assets, prefill, mutation }: PredictionFormProps) {
  const lastRequestRef = useRef<PredictionRequest | null>(null);

  const form = useForm<PredictionFormValues>({
    resolver: zodResolver(predictionSchema),
    defaultValues: {
      event_text: prefill.eventText ?? '',
      asset_ticker: prefill.assetTicker ?? '',
      window_type: 'post_event_5d',
      event_type: prefill.eventType ?? '',
      event_subtype: '',
      event_condition: '',
      save: false,
    },
  });

  function onSubmit(values: PredictionFormValues) {
    const request: PredictionRequest = {
      event_text: values.event_text,
      asset_ticker: values.asset_ticker,
      window_type: values.window_type as PredictionRequest['window_type'],
      event_type: values.event_type || null,
      event_subtype: values.event_subtype || null,
      event_condition: values.event_condition || null,
      save: values.save,
    };
    lastRequestRef.current = request;
    mutation.mutate(request);
  }

  const error = mutation.error ? toApiError(mutation.error) : null;

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4" aria-label="事件研究预测表单">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="event-text">事件文本</Label>
        <textarea
          id="event-text"
          rows={6}
          placeholder="输入事件标题、摘要或描述（1..20000 字符）"
          className="rounded-md border bg-transparent px-3 py-2 text-sm"
          style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
          {...form.register('event_text')}
        />
        {form.formState.errors.event_text && (
          <p className="text-xs text-red-400">{form.formState.errors.event_text.message}</p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="asset-ticker">目标资产</Label>
        <Input
          id="asset-ticker"
          list="event-study-assets"
          placeholder="如 000001.SH"
          {...form.register('asset_ticker')}
        />
        <datalist id="event-study-assets">
          {assets.map((asset) => (
            <option key={asset.ticker} value={asset.ticker}>
              {asset.name}（{asset.market}）
            </option>
          ))}
        </datalist>
        {form.formState.errors.asset_ticker && (
          <p className="text-xs text-red-400">{form.formState.errors.asset_ticker.message}</p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="window-type">预测窗口</Label>
        <select
          id="window-type"
          className="h-9 rounded-md border bg-transparent px-3 text-sm"
          style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg)' }}
          {...form.register('window_type')}
        >
          <option value="pre_event_5d">事件日前 5 日</option>
          <option value="event_day">事件当日</option>
          <option value="post_event_5d">事件日后 5 日</option>
        </select>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="event-type">事件类型（可选）</Label>
          <Input id="event-type" {...form.register('event_type')} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="event-subtype">事件子类型（可选）</Label>
          <Input id="event-subtype" {...form.register('event_subtype')} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="event-condition">关键条件（可选）</Label>
          <Input id="event-condition" {...form.register('event_condition')} />
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <Checkbox {...form.register('save')} />
        保存本次预测（落库供追踪，不改变返回）
      </label>

      {error && (
        <ErrorState
          error={error}
          onRetry={error.retryable && lastRequestRef.current ? () => mutation.mutate(lastRequestRef.current as PredictionRequest) : undefined}
        />
      )}

      <div className="flex justify-end">
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? '预测中…' : '预测'}
        </Button>
      </div>
    </form>
  );
}
