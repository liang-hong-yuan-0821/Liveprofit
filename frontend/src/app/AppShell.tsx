import { Outlet } from 'react-router';
import { useUiPreferenceStore } from '../stores/uiPreferenceStore';
import { SidebarNav } from './components/SidebarNav';

// 应用壳：一级导航 + 主题切换 + 内容出口。
export function AppShell() {
  const collapsed = useUiPreferenceStore((state) => state.sidebarCollapsed);

  return (
    <div className="flex min-h-screen">
      <aside
        className="shrink-0 border-r transition-[width]"
        style={{ width: collapsed ? '3.5rem' : '14rem', borderColor: 'var(--color-border)' }}
      >
        <SidebarNav />
      </aside>
      <main className="min-w-0 flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
