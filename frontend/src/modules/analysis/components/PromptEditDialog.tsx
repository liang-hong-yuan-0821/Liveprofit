// Agent 提示词编辑弹窗（Agent tab 静态拓扑与任务详情节点弹窗共用）。
// 当前生效提示词 = override_prompt ?? default_prompt 填入 textarea；
// 保存 PUT 全局覆盖（对新建任务生效，进行中任务不受影响）、恢复默认 DELETE。
// 订阅全局提示词列表缓存（enabled 门控防 refetchOnMount 二次拉取，沿用 NodeLogsDialog 教训）。

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../../shared/ui/dialog';
import { Button } from '../../../shared/ui/button';
import { MarkdownView } from '../../../shared/ui/markdown';
import { toApiError } from '../../../api/client';
import { useAgentsPromptsQuery, useResetAgentPromptMutation, useUpdateAgentPromptMutation } from '../pages/agents/queries';

const MAX_PROMPT_LENGTH = 20000;

export interface PromptEditNode {
  node_id: string;
  label: string;
}

interface PromptEditDialogProps {
  node: PromptEditNode | null;
  onClose: () => void;
  /** 编辑上下文说明（任务详情内注明"对新建任务生效"） */
  contextNote?: string;
}

// 注意：不得带 g 标志——RegExp.test 的 lastIndex 状态会在多次求值间残留
const PLACEHOLDER_RE = /\{[\w]+\}/;

export function PromptEditDialog({ node, onClose, contextNote }: PromptEditDialogProps) {
  const open = node !== null;
  // enabled 门控：弹窗打开才订阅（防每页挂载即拉全量提示词列表；打开瞬间
  // 经同 query key 复用 Agent 页已加载缓存，零额外请求——NodeLogsDialog 同款教训）
  const promptsQuery = useAgentsPromptsQuery(open);
  const updateMutation = useUpdateAgentPromptMutation();
  const resetMutation = useResetAgentPromptMutation();

  const entry = useMemo(() => {
    if (!node || !promptsQuery.data) return null;
    return promptsQuery.data.items.find((d) => d.node_id === node.node_id) ?? null;
  }, [node, promptsQuery.data]);

  const [text, setText] = useState('');
  const [preview, setPreview] = useState(false);
  // 已回填的节点 id：只在首次打开/切换节点时回填，entry 对象更新（保存后
  // refetch）不回填——防止用户输入被服务端旧文本覆盖
  const hydratedNodeIdRef = useRef<string | null>(null);
  // 用户是否已交互（输入/清空）：显式标记，不用 text 是否为空推断——
  // 清空后 entry 因 refetch 更新会重跑 effect，text==='' 判据会把服务端
  // 文本重新写回（清空被撤销），见第 2 轮 Code Review major
  const userEditedRef = useRef(false);

  // 首次打开/切换节点时回填当前生效提示词。回填条件：
  // ① node_id 变化（含 null→node）→ 重置 userEdited 并回填；
  // ② 同一节点下 entry 从无到有（打开瞬间 prompts 查询尚未返回）且用户未交互；
  // ③ 用户已交互（userEditedRef）→ 任何 entry 更新均不回填
  useEffect(() => {
    if (!node) {
      // 关闭复位：重开同节点走"节点变化"分支重新回填（不残留上次草稿/交互标记）
      hydratedNodeIdRef.current = null;
      userEditedRef.current = false;
      return;
    }
    if (hydratedNodeIdRef.current === node.node_id) {
      if (!entry || userEditedRef.current) return;
    } else {
      hydratedNodeIdRef.current = node.node_id;
      userEditedRef.current = false;
    }
    // 打开瞬间 prompts 查询可能尚未返回（entry=null）→ 先置空，entry 到达后回填
    setText(entry ? (entry.override_prompt ?? entry.default_prompt) : '');
    setPreview(false);
  }, [node, entry]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasPlaceholders = useMemo(
    () => !!entry && PLACEHOLDER_RE.test(entry.default_prompt),
    [entry],
  );
  const lengthValid = text.trim().length > 0 && text.length <= MAX_PROMPT_LENGTH;
  const pending = updateMutation.isPending || resetMutation.isPending;
  const error = updateMutation.error ?? resetMutation.error;

  function handleSave() {
    if (!node || !lengthValid) return;
    updateMutation.mutate({ nodeId: node.node_id, promptText: text });
  }

  function handleReset() {
    if (!node) return;
    resetMutation.mutate(node.node_id, { onSuccess: () => setText(entry?.default_prompt ?? '') });
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>编辑 Agent 提示词 · {node?.label}</DialogTitle>
          <DialogDescription>
            保存后对新建任务生效（运行中/排队中任务不受影响）
            {contextNote ? ` · ${contextNote}` : ''}
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto">
          {hasPlaceholders && !preview && (
            <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              {`默认提示词中的 {xxx} 为运行时注入变量（数据/日期/上下文占位符）。覆盖提示词将原样生效，建议保留报告结构要求。`}
            </p>
          )}
          <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            <button
              type="button"
              className={preview ? '' : 'font-semibold underline'}
              onClick={() => setPreview(false)}
            >
              编辑
            </button>
            <button
              type="button"
              className={preview ? 'font-semibold underline' : ''}
              onClick={() => setPreview(true)}
            >
              预览
            </button>
            <span className="ml-auto">{text.length} / {MAX_PROMPT_LENGTH}</span>
          </div>
          {preview ? (
            <div className="min-h-[200px]">
              <MarkdownView content={text} />
            </div>
          ) : (
            <textarea
              className="min-h-[260px] w-full resize-y rounded-md border p-2 font-mono text-xs"
              style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg)' }}
              value={text}
              onChange={(e) => {
                userEditedRef.current = true;
                setText(e.target.value);
              }}
            />
          )}
          {!lengthValid && (
            <p className="text-xs" style={{ color: 'var(--color-danger, #ef4444)' }}>
              {text.trim().length === 0 ? '提示词不能为空' : `提示词超长（上限 ${MAX_PROMPT_LENGTH} 字符）`}
            </p>
          )}
          {error && (
            <p className="text-xs" style={{ color: 'var(--color-danger, #ef4444)' }}>
              {toApiError(error).message}
            </p>
          )}
        </div>

        <DialogFooter>
          {entry?.has_override && (
            <Button variant="ghost" disabled={pending} onClick={handleReset}>
              恢复默认
            </Button>
          )}
          <Button variant="outline" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button disabled={pending || !lengthValid} onClick={handleSave}>
            {updateMutation.isPending ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
