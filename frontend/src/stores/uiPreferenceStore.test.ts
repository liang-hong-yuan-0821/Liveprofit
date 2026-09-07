import { beforeEach, describe, expect, it } from 'vitest';
import { UI_PREFERENCE_STORAGE_KEY, useUiPreferenceStore } from './uiPreferenceStore';

beforeEach(() => {
  localStorage.clear();
  useUiPreferenceStore.setState({ themeMode: 'dark', sidebarCollapsed: false });
});

describe('uiPreferenceStore', () => {
  it('默认深色主题、侧栏展开', () => {
    expect(useUiPreferenceStore.getState().themeMode).toBe('dark');
    expect(useUiPreferenceStore.getState().sidebarCollapsed).toBe(false);
  });

  it('toggleTheme 在 dark/light 之间切换并写入本机持久化', () => {
    useUiPreferenceStore.getState().toggleTheme();
    expect(useUiPreferenceStore.getState().themeMode).toBe('light');

    const saved = JSON.parse(localStorage.getItem(UI_PREFERENCE_STORAGE_KEY) ?? '{}');
    expect(saved.state?.themeMode).toBe('light');

    useUiPreferenceStore.getState().toggleTheme();
    expect(useUiPreferenceStore.getState().themeMode).toBe('dark');
  });

  it('setSidebarCollapsed 持久化折叠状态', () => {
    useUiPreferenceStore.getState().setSidebarCollapsed(true);
    expect(useUiPreferenceStore.getState().sidebarCollapsed).toBe(true);

    const saved = JSON.parse(localStorage.getItem(UI_PREFERENCE_STORAGE_KEY) ?? '{}');
    expect(saved.state?.sidebarCollapsed).toBe(true);
  });

  it('persist 只保存偏好值本身，不保存 action', () => {
    useUiPreferenceStore.getState().toggleTheme();
    const saved = JSON.parse(localStorage.getItem(UI_PREFERENCE_STORAGE_KEY) ?? '{}');
    expect(saved.state?.toggleTheme).toBeUndefined();
    expect(saved.state?.setSidebarCollapsed).toBeUndefined();
  });
});
