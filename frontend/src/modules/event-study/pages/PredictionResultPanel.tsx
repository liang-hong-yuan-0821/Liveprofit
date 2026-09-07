import { Badge } from '../../../shared/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import {
  DIRECTION_LABELS,
  type PredictionViewModel,
} from './mappers/toPredictionViewModel';

// 预测结果面板：方向/CAR/置信度/样本统计/相似事件全部来自服务端；
// 样本不足/结果为空按 note 正常呈现，不生成前端结论。
export function PredictionResultPanel({ result }: { result: PredictionViewModel }) {
  const direction = result.direction ? (DIRECTION_LABELS[result.direction] ?? result.direction) : '—';

  return (
    <Card>
      <CardHeader>
        <CardTitle>预测结果</CardTitle>
        <Badge variant={result.direction === 'up' ? 'destructive' : result.direction === 'down' ? 'success' : 'secondary'}>
          {direction}
        </Badge>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm md:grid-cols-3">
          <dt style={{ color: 'var(--color-fg-muted)' }}>预测收益（CAR）</dt>
          <dd>{result.predictedReturn !== null ? `${(result.predictedReturn * 100).toFixed(2)}%` : '—'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>置信度</dt>
          <dd>{result.confidence !== null ? `${(result.confidence * 100).toFixed(0)}%` : '—'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>样本数</dt>
          <dd>{result.sampleCount ?? '—'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>平均 CAR</dt>
          <dd>{result.avgCar !== null ? `${(result.avgCar * 100).toFixed(2)}%` : '—'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>胜率</dt>
          <dd>{result.winRate !== null ? `${(result.winRate * 100).toFixed(0)}%` : '—'}</dd>
        </dl>

        {result.supplementEvents.length > 0 && (
          <div className="mt-3">
            <p className="mb-1 text-sm font-medium">相似历史事件</p>
            <ul className="flex flex-col gap-1 text-sm">
              {result.supplementEvents.map((event) => (
                <li key={event.eventId} className="flex justify-between gap-4">
                  <span className="truncate">{event.title}</span>
                  <span className="shrink-0 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    相似度 {(event.similarity * 100).toFixed(0)}% · 权重 {event.weight.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {result.note && (
          <p className="mt-3 rounded-md border p-2 text-sm" style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg-muted)' }}>
            {result.note}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
