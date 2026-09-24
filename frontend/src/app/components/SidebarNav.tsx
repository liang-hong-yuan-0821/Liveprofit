import { Link, NavLink } from 'react-router';
import { BarChart3, Bot, ChevronLeft, ChevronRight, FlaskConical, Star, Activity, Newspaper } from 'lucide-react';
import { useUiPreferenceStore } from '../../stores/uiPreferenceStore';
import { ThemeToggle } from './ThemeToggle';

const NAV_ITEMS = [
  { to: '/market', label: '大盘', icon: BarChart3 },
  { to: '/watchlist', label: '自选', icon: Star },
  { to: '/ai', label: 'AI 研究', icon: Bot },
  { to: '/daily-research', label: '每日研究', icon: Newspaper },
  { to: '/event-study', label: '事件研究', icon: FlaskConical },
] as const;

export function SidebarNav({ collapsed = false, allowCollapse = true, onNavigate }: { collapsed?: boolean; allowCollapse?: boolean; onNavigate?: () => void }) {
  const setSidebarCollapsed = useUiPreferenceStore((state) => state.setSidebarCollapsed);

  return (
    <div className="flex h-full flex-col py-3">
      <Link to="/market" onClick={onNavigate} className="mb-8 flex items-center gap-3 px-4 py-4" title="返回大盘">
        <span className="brand-mark flex size-10 shrink-0 items-center justify-center rounded-xl"><Activity className="size-6" /></span>
        {!collapsed && <span><span className="block text-lg font-semibold tracking-tight">Liveprofit</span><span className="text-[10px] tracking-[.16em] text-[var(--color-fg-muted)]">投资研究工作台</span></span>}
      </Link>
      {!collapsed && <p className="mb-3 px-5 text-[10px] font-medium tracking-[.18em] text-[var(--color-fg-muted)]">WORKSPACE / 工作区</p>}

      <nav className="flex flex-col gap-1 px-2">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            onClick={onNavigate}
            title={collapsed ? label : undefined}
            aria-label={label}
            className="nav-item flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium"
          >
            <Icon className="size-5 shrink-0" aria-hidden />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto flex flex-col gap-1 border-t px-2 py-3" style={{ borderColor: 'var(--color-border)' }}>
        <ThemeToggle collapsed={collapsed} />
        {allowCollapse && <button
          type="button"
          onClick={() => setSidebarCollapsed(!collapsed)}
          title={collapsed ? '展开侧栏' : '折叠侧栏'}
          className="flex items-center gap-2 rounded px-2 py-2 text-sm"
          style={{ color: 'var(--color-fg-muted)' }}
        >
          {collapsed ? <ChevronRight className="size-4" aria-hidden /> : <ChevronLeft className="size-4" aria-hidden />}
          {!collapsed && <span>折叠侧栏</span>}
        </button>}
      </div>
    </div>
  );
}
