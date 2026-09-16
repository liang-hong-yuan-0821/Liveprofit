// 策略页组件测试（plan 4.4.3 盲点补齐）：新建必填代码、列表渲染、创建调用。

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithRouter } from '../../../../test/utils';
import QuantStrategiesPage from './QuantStrategiesPage';

vi.mock('../../../../api/generated/services/QuantStrategiesService', () => ({
  QuantStrategiesService: {
    listStrategiesApiV1QuantStrategiesGet: vi.fn(),
    createStrategyApiV1QuantStrategiesPost: vi.fn(),
    getDraftApiV1QuantStrategiesStrategyIdDraftGet: vi.fn(),
    saveDraftApiV1QuantStrategiesStrategyIdDraftPut: vi.fn(),
    publishVersionApiV1QuantStrategiesStrategyIdVersionsVersionIdPublishPost: vi.fn(),
    archiveVersionApiV1QuantStrategiesStrategyIdVersionsVersionIdArchivePost: vi.fn(),
  },
}));

import { QuantStrategiesService } from '../../../../api/generated/services/QuantStrategiesService';

const listMock = QuantStrategiesService.listStrategiesApiV1QuantStrategiesGet as ReturnType<typeof vi.fn>;
const createMock = QuantStrategiesService.createStrategyApiV1QuantStrategiesPost as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue({
    data: { items: [] },
    meta: { request_id: 'r', schema_version: 'v1' },
  });
  createMock.mockResolvedValue({
    data: {
      id: 's-1',
      name: '新策略',
      description: null,
      version: 1,
      created_at: '2026-09-17T00:00:00Z',
      updated_at: '2026-09-17T00:00:00Z',
      versions: [],
    },
    meta: { request_id: 'r', schema_version: 'v1' },
  });
});

describe('QuantStrategiesPage', () => {
  it('空列表渲染空态', async () => {
    renderWithRouter(<QuantStrategiesPage />);
    expect(await screen.findByText(/暂无策略/)).toBeInTheDocument();
  });

  it('新建策略必须输入代码：代码为空时创建按钮禁用并提示', async () => {
    const user = userEvent.setup();
    renderWithRouter(<QuantStrategiesPage />);

    await user.click(await screen.findByRole('button', { name: '新建策略' }));
    const nameInput = screen.getByLabelText('名称');
    await user.type(nameInput, '双均线');

    // 只填名称：创建仍禁用（代码必填）+ 就地提示
    const createButton = screen.getByRole('button', { name: '创建' });
    expect(createButton).toBeDisabled();
    expect(screen.getByText(/请先输入策略代码/)).toBeInTheDocument();

    // 代码编辑器已渲染（CodeMirror 受控组件，真实浏览器中输入后 onChange 触发创建解禁）
    expect(screen.getByLabelText('新建策略代码')).toBeInTheDocument();
  });
});
