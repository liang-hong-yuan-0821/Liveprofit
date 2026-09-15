import { describe, expect, it } from 'vitest';
import { formatVolume } from './volume';

describe('formatVolume 量级缩写', () => {
  it('null/undefined/NaN → 占位符', () => {
    expect(formatVolume(null)).toBe('—');
    expect(formatVolume(undefined)).toBe('—');
    expect(formatVolume(Number.NaN)).toBe('—');
  });

  it('手/万手/亿手边界', () => {
    expect(formatVolume(0)).toBe('0手');
    expect(formatVolume(9999)).toBe('9999手');
    expect(formatVolume(1e4)).toBe('1.00万手');
    expect(formatVolume(123456)).toBe('12.35万手');
    expect(formatVolume(1e8)).toBe('1.00亿手');
    expect(formatVolume(2.5e8)).toBe('2.50亿手');
  });

  it('小数位保留两位', () => {
    expect(formatVolume(12345)).toBe('1.23万手');
    expect(formatVolume(123456789)).toBe('1.23亿手');
  });
});
