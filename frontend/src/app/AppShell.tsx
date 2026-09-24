import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router';
import { Menu, Sparkles } from 'lucide-react';
import { useUiPreferenceStore } from '../stores/uiPreferenceStore';
import { SidebarNav } from './components/SidebarNav';
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '../shared/ui/dialog';

export function AppShell() {
  const preferredCollapsed = useUiPreferenceStore((state) => state.sidebarCollapsed);
  const [compact, setCompact] = useState(() => window.innerWidth < 1024);
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1023px)');
    const update = () => setCompact(query.matches);
    update(); query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => { setMenuOpen(false); }, [location.pathname]);
  const collapsed = compact || preferredCollapsed;
  return (
    <div className="app-shell flex min-h-dvh">
      <a href="#main-content" className="fixed left-4 top-2 z-[100] -translate-y-20 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-[var(--color-accent-foreground)] focus:translate-y-0">跳到主要内容</a>
      <aside className="app-sidebar sticky top-0 hidden h-dvh shrink-0 border-r transition-[width] md:block" style={{ width: collapsed ? '4.5rem' : '15rem', borderColor: 'var(--color-border)' }}>
        <SidebarNav collapsed={collapsed} allowCollapse={!compact} />
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)]/90 px-4 backdrop-blur-xl md:hidden">
          <span className="flex items-center gap-2 font-semibold"><Sparkles className="size-4 text-[var(--color-accent-bright)]" />Liveprofit</span>
          <Dialog open={menuOpen} onOpenChange={setMenuOpen}>
            <DialogTrigger asChild><button type="button" aria-label="打开导航菜单" className="rounded-lg p-2"><Menu className="size-5" /></button></DialogTrigger>
            <DialogContent className="h-[min(36rem,85dvh)] max-w-xs p-3">
              <DialogTitle className="sr-only">导航菜单</DialogTitle>
              <DialogDescription className="sr-only">选择工作区页面或切换主题</DialogDescription>
              <SidebarNav collapsed={false} allowCollapse={false} onNavigate={() => setMenuOpen(false)} />
            </DialogContent>
          </Dialog>
        </header>
        <main id="main-content" tabIndex={-1} className="app-main mx-auto min-w-0 max-w-[1800px] p-4 outline-none lg:p-7 xl:p-9"><Outlet /></main>
      </div>
    </div>
  );
}
