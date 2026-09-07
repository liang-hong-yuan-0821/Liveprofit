import type { PredictionData } from '../../../../api/generated';

// PredictionData 的 prediction/template_stats 为服务端宽松结构，展示层做防御性映射；
// 所有数值与结论来自服务端，样本不足/结果为空按 note 正常呈现，前端不推导结论。
export interface SupplementEventViewModel {
  eventId: number;
  title: string;
  similarity: number;
  weight: number;
}

export interface PredictionViewModel {
  direction: string | null;
  predictedReturn: number | null;
  confidence: number | null;
  sampleCount: number | null;
  avgCar: number | null;
  winRate: number | null;
  supplementEvents: SupplementEventViewModel[];
  note: string | null;
}

export const DIRECTION_LABELS: Record<string, string> = {
  up: '看多',
  down: '看空',
  neutral: '中性',
};

function num(value: unknown): number | null {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

export function toPredictionViewModel(data: PredictionData): PredictionViewModel {
  const prediction = data.prediction ?? {};
  const stats = data.template_stats ?? {};
  const supplement = Array.isArray(data.supplement_events) ? data.supplement_events : [];

  return {
    direction: str(prediction.direction),
    predictedReturn: num(prediction.predicted_return),
    confidence: num(prediction.confidence),
    sampleCount: num(stats.sample_count),
    avgCar: num(stats.avg_car),
    winRate: num(stats.win_rate),
    supplementEvents: supplement
      .filter((item) => typeof item === 'object' && item !== null && typeof item.event_id === 'number')
      .map((item) => ({
        eventId: item.event_id as number,
        title: str(item.title) ?? '—',
        similarity: num(item.similarity) ?? 0,
        weight: num(item.weight) ?? 0,
      })),
    note: data.note ?? null,
  };
}
