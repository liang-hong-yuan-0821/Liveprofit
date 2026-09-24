import { toApiError } from '../../api/client';
const messages: Record<string, string> = {
  REQUEST_TIMEOUT: '请求耗时较长，请稍后重试。', INTERNAL_ERROR: '服务暂时无法完成请求，请稍后再试。',
  TASK_NOT_FOUND: '此任务不存在或已被删除。', INVALID_ENVELOPE: '收到的数据格式异常，请联系维护人员。',
  UNKNOWN: '暂时无法完成请求，请检查连接后重试。',
};
export function userMessage(error: unknown) { const value = toApiError(error); return messages[value.code] ?? (value.status === 409 ? '数据已发生变化，请重新加载后再操作。' : value.status === 503 || value.status === 504 ? '服务暂时繁忙，请稍后手动重试。' : '当前操作未能完成，请查看详情或稍后再试。'); }
