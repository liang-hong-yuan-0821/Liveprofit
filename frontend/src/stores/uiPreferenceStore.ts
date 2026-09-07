import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// 仅存被至少两个独立组件消费的跨页面 UI 偏好：
// themeMode 由 ThemeToggle、AppProviders 消费；sidebarCollapsed 由 AppShell、SidebarNav 消费。
// 任务流、表单、筛选、报告展开状态与 API 数据不得进入本 Store。
export type ThemeMode = 'light' | 'dark';

interface UiPreferenceState {
  themeMode: ThemeMode;
  sidebarCollapsed: boolean;
  toggleTheme: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

export const UI_PREFERENCE_STORAGE_KEY = 'liveprofit-ui-preferences';

export const useUiPreferenceStore = create<UiPreferenceState>()(
  persist(
    (set) => ({
      themeMode: 'dark',
      sidebarCollapsed: false,
      toggleTheme: () =>
        set((state) => ({ themeMode: state.themeMode === 'dark' ? 'light' : 'dark' })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
    }),
    {
      name: UI_PREFERENCE_STORAGE_KEY,
      // 只持久化偏好值本身，不持久化 action
      partialize: (state) => ({ themeMode: state.themeMode, sidebarCollapsed: state.sidebarCollapsed }),
    },
  ),
);
