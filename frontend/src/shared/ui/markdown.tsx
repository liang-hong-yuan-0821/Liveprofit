import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import { cn } from './utils';

// 任务详情页 markdown 内容的统一渲染组件：GFM + 单换行断行，深色 prose 样式。
// react-markdown 默认转义原始 HTML（无 rehype-raw），内容为内核生成的受信 markdown，无需净化。
export function MarkdownView({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn('prose prose-sm prose-invert max-w-none [&_table]:block [&_table]:overflow-x-auto', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{content}</ReactMarkdown>
    </div>
  );
}
