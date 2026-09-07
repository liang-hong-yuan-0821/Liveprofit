import { Moon, Sun } from 'lucide-react';
import { useUiPreferenceStore } from '../../stores/uiPreferenceStore';

export function ThemeToggle({ collapsed }: { collapsed: boolean }) {
  const themeMode = useUiPreferenceStore((state) => state.themeMode);
  const toggleTheme = useUiPreferenceStore((state) => state.toggleTheme);
  const isDark = themeMode === 'dark';

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={isDark ? '切换为浅色主题' : '切换为深色主题'}
      className="flex items-center gap-2 rounded px-2 py-2 text-sm"
      style={{ color: 'var(--color-fg-muted)' }}
    >
      {isDark ? <Moon className="size-4" aria-hidden /> : <Sun className="size-4" aria-hidden />}
      {!collapsed && <span>{isDark ? '深色' : '浅色'}</span>}
    </button>
  );
}
