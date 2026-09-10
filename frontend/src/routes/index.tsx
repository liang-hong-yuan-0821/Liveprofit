import { lazy, Suspense, type ReactNode } from 'react';
import { Navigate, createBrowserRouter, useLocation } from 'react-router';
import { AppErrorBoundary } from '../app/AppErrorBoundary';
import { AppShell } from '../app/AppShell';
import { RouteErrorBoundary } from '../app/RouteErrorBoundary';
import { LoadingState } from '../shared/feedback/LoadingState';
import { NotFoundState } from '../shared/feedback/NotFoundState';

// routes/ 仅做 URL → Module Page 的薄装配与懒加载，不承载任何业务逻辑。
const MarketOverviewPage = lazy(() => import('../modules/market/pages/MarketOverviewPage'));
const WatchlistPage = lazy(() => import('../modules/watchlist/pages/WatchlistPage'));
const AiDashboardPage = lazy(() => import('../modules/analysis/pages/dashboard/AiDashboardPage'));
const AiTasksPage = lazy(() => import('../modules/analysis/pages/tasks/AiTasksPage'));
const AiTaskDetailPage = lazy(() => import('../modules/analysis/pages/task-detail/AiTaskDetailPage'));
const EventStudyPage = lazy(() => import('../modules/event-study/pages/EventStudyPage'));

function page(node: ReactNode) {
  return <Suspense fallback={<LoadingState label="页面加载中…" />}>{node}</Suspense>;
}

// /ai/event-study 旧入口 → /event-study，透传 query（如宏观卡片跳转的 ?event_id=101）
function LegacyEventStudyRedirect() {
  const location = useLocation();
  return <Navigate to={`/event-study${location.search}`} replace />;
}

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    // 根路由错误边界：捕获应用壳/渲染层异常
    errorElement: <AppErrorBoundary />,
    children: [
      { index: true, element: <Navigate to="/market" replace /> },
      { path: 'market', element: page(<MarketOverviewPage />), errorElement: <RouteErrorBoundary /> },
      { path: 'watchlist', element: page(<WatchlistPage />), errorElement: <RouteErrorBoundary /> },
      { path: 'ai', element: page(<AiDashboardPage />), errorElement: <RouteErrorBoundary /> },
      { path: 'ai/tasks', element: page(<AiTasksPage />), errorElement: <RouteErrorBoundary /> },
      { path: 'ai/tasks/:taskId', element: page(<AiTaskDetailPage />), errorElement: <RouteErrorBoundary /> },
      { path: 'event-study', element: page(<EventStudyPage />), errorElement: <RouteErrorBoundary /> },
      { path: 'ai/event-study', element: <LegacyEventStudyRedirect /> },
      { path: 'ai/event-study/review', element: <Navigate to="/event-study?tab=review" replace /> },
      { path: '*', element: <NotFoundState to="/market" label="返回大盘" /> },
    ],
  },
]);
