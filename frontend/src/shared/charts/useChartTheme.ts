import { useUiPreferenceStore } from '../../stores/uiPreferenceStore';
export const CHART_THEMES = {
  dark: { text: '#acbacb', grid: '#354253', neutral: '#eaf0f8', surface: '#232a34' },
  light: { text: '#636978', grid: '#d8d4cc', neutral: '#242a36', surface: '#fffefb' },
};
export function useChartTheme() { return CHART_THEMES[useUiPreferenceStore(state => state.themeMode)]; }
