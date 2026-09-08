// Query Key 工厂：所有 REST Query Key 经此生成，禁止页面手写字符串数组。
// 各 Module 的页面私有 Query 封装使用对应域实例，Cursor 分页筛选进 list filters。

export function makeQueryKeys(domain: string) {
  return {
    all: [domain] as const,
    lists: () => [domain, 'list'] as const,
    list: (filters: Record<string, unknown>) => [domain, 'list', filters] as const,
    details: () => [domain, 'detail'] as const,
    detail: (id: string) => [domain, 'detail', id] as const,
  };
}

export const queryKeys = {
  marketAssets: makeQueryKeys('market-assets'),
  marketBars: makeQueryKeys('market-bars'),
  hotConcepts: makeQueryKeys('hot-concepts'),
  macroInformation: makeQueryKeys('macro-information'),
  analysisDashboard: makeQueryKeys('analysis-dashboard'),
  analysisTasks: makeQueryKeys('analysis-tasks'),
  analysisTask: {
    ...makeQueryKeys('analysis-task'),
    executionLogs: (taskId: string) => ['analysis-task', 'execution-logs', taskId] as const,
    graphTopology: (taskId: string) => ['analysis-task', 'graph-topology', taskId] as const,
  },
  analysisReport: makeQueryKeys('analysis-report'),
  watchlists: makeQueryKeys('watchlists'),
  portfolios: makeQueryKeys('portfolios'),
  eventStudyAssets: makeQueryKeys('event-study-assets'),
  eventStudyReview: makeQueryKeys('event-study-review'),
} as const;
