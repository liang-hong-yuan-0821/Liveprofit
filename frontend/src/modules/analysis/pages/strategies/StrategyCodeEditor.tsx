// 策略代码编辑器：CodeMirror（Python 着色/行号）+ 本地 ruff 格式化按钮。

import CodeMirror from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';

import { Button } from '../../../../shared/ui/button';
import { useFormatStrategySourceMutation } from './queries';

interface StrategyCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}

export function StrategyCodeEditor({ value, onChange, ariaLabel }: StrategyCodeEditorProps) {
  const format = useFormatStrategySourceMutation();

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          def strategy(context): 返回七键字典（action/score/entry_price/stop_loss/take_profit/sell_ratio/reason）
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={format.isPending}
          onClick={() => format.mutate(value, { onSuccess: (dto) => onChange(dto.source_code) })}
        >
          {format.isPending ? '格式化中…' : '格式化'}
        </Button>
      </div>
      <CodeMirror
        value={value}
        onChange={onChange}
        height="320px"
        theme="dark"
        aria-label={ariaLabel}
        extensions={[python()]}
        basicSetup={{ lineNumbers: true, foldGutter: true, autocompletion: false }}
      />
      {format.isError && (
        <p className="text-xs text-red-400" role="alert">
          格式化失败：{format.error instanceof Error ? format.error.message : '未知错误'}
        </p>
      )}
    </div>
  );
}
