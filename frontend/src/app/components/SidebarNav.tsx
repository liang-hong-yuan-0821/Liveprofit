import { Link, NavLink } from 'react-router';
import { BarChart3, Bot, ChevronLeft, ChevronRight, Star } from 'lucide-react';
import { useUiPreferenceStore } from '../../stores/uiPreferenceStore';
import { ThemeToggle } from './ThemeToggle';

const NAV_ITEMS = [
  { to: '/market', label: '大盘', icon: BarChart3 },
  { to: '/watchlist', label: '自选', icon: Star },
  { to: '/ai', label: 'AI', icon: Bot },
] as const;

export function SidebarNav() {
  const collapsed = useUiPreferenceStore((state) => state.sidebarCollapsed);
  const setSidebarCollapsed = useUiPreferenceStore((state) => state.setSidebarCollapsed);

  return (
    <div className="flex h-full flex-col" style={{ backgroundColor: 'var(--color-surface)' }}>
      <div className="flex items-center gap-2 px-3 py-4">
        <Link to="/market" className="truncate font-semibold" title="返回大盘">
          {collapsed ? 'LP' : 'Liveprofit'}
        </Link>
      </div>

      <nav className="flex flex-col gap-1 px-2">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/ai'}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              `flex items-center gap-2 rounded px-2 py-2 text-sm ${
                isActive ? 'font-medium' : ''
              }`
            }
            style={({ isActive }) =>
              isActive
                ? { backgroundColor: 'var(--color-accent)', color: '#fff' }
                : { color: 'var(--color-fg-muted)' }
            }
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto flex flex-col gap-1 border-t px-2 py-3" style={{ borderColor: 'var(--color-border)' }}>
        <ThemeToggle collapsed={collapsed} />
        <button
          type="button"
          onClick={() => setSidebarCollapsed(!collapsed)}
          title={collapsed ? '展开侧栏' : '折叠侧栏'}
          className="flex items-center gap-2 rounded px-2 py-2 text-sm"
          style={{ color: 'var(--color-fg-muted)' }}
        >
          {collapsed ? <ChevronRight className="size-4" aria-hidden /> : <ChevronLeft className="size-4" aria-hidden />}
          {!collapsed && <span>折叠侧栏</span>}
        </button>
      </div>
    </div>
  );
}
