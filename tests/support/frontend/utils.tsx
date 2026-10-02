import type { ReactElement, ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import {
  RouterProvider,
  createMemoryRouter,
  useLocation,
  useParams,
  type InitialEntry,
} from 'react-router';

// 测试工具：独立 QueryClient（不重试）+ 内存路由（含任务详情/位置哨兵，用于断言跳转与 URL 状态）
export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

export function renderWithRouter(
  ui: ReactElement,
  options: { initialEntries?: InitialEntry[]; queryClient?: QueryClient } = {},
) {
  const queryClient = options.queryClient ?? createTestQueryClient();
  const wrapped = (
    <QueryClientProvider client={queryClient}>
      {ui}
      <LocationSentinel />
    </QueryClientProvider>
  );
  const router = createMemoryRouter(
    [
      // 任务详情路径也渲染同一 UI（页面测试的入口路径）+ 哨兵，用于断言跳转与 taskId
      {
        path: '/ai/tasks/:taskId',
        element: (
          <>
            {wrapped}
            <TaskDetailSentinel />
          </>
        ),
      },
      {
        path: '*',
        element: wrapped,
      },
    ],
    { initialEntries: options.initialEntries ?? ['/'] },
  );
  render(<RouterProvider router={router} />);
  return { queryClient };
}

// 任务详情哨兵路由：测试断言"创建成功后跳转到 /ai/tasks/:taskId"
function TaskDetailSentinel() {
  const { taskId } = useParams();
  return <div data-testid="task-detail-sentinel">task-detail:{taskId}</div>;
}

// 位置哨兵：测试断言 search params 的清理/恢复
function LocationSentinel() {
  const location = useLocation();
  return <span data-testid="location" data-search={location.search} data-pathname={location.pathname} />;
}

export function withQueryClient(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}
