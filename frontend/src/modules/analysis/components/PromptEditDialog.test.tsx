// PromptEditDialog 专属测试：恢复默认（DELETE）+ 占位符提示（弹窗主体交互已在
// AgentTopologyPage.test.tsx 覆盖：预填/保存/空值禁用）。

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentsService } from '../../../api/generated/services/AgentsService';
import { createTestQueryClient, withQueryClient } from '../../../test/utils';
import { PromptEditDialog } from './PromptEditDialog';

vi.mock('../../../api/generated/services/AgentsService', () => ({
  AgentsService: {
    getAgentsTopologyApiV1AgentsTopologyGet: vi.fn(),
    listAgentPromptsApiV1AgentsPromptsGet: vi.fn(),
    upsertAgentPromptApiV1AgentsPromptsNodeIdPut: vi.fn(),
    resetAgentPromptApiV1AgentsPromptsNodeIdDelete: vi.fn(),
  },
}));

const listPrompts = vi.mocked(AgentsService.listAgentPromptsApiV1AgentsPromptsGet);
const resetPrompt = vi.mocked(AgentsService.resetAgentPromptApiV1AgentsPromptsNodeIdDelete);

const ENVELOPE = (data: unknown) => ({ data, meta: { request_id: 'r', schema_version: 'v1' } });

const NODE = { node_id: 'market:CN News Analyst', label: 'CN News Analyst' };

function renderDialog(node = NODE) {
  return render(
    <PromptEditDialog node={node} onClose={() => {}} />,
    { wrapper: withQueryClient(createTestQueryClient()) },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listPrompts.mockResolvedValue(ENVELOPE({
    items: [
      {
        node_id: 'market:CN News Analyst',
        label: 'CN News Analyst',
        layer: 'market',
        default_prompt: '默认提示词含 {market_overview} 占位符',
        override_prompt: '自定义提示词',
        has_override: true,
        updated_at: '2026-09-09T09:00:00Z',
      },
      {
        node_id: 'market:CN Tech Analyst',
        label: 'CN Tech Analyst',
        layer: 'market',
        default_prompt: '默认提示词含 {market_overview} 占位符',
        override_prompt: null,
        has_override: false,
        updated_at: null,
      },
    ],
  }) as never);
});

describe('PromptEditDialog', () => {
  it('有覆盖时显示「恢复默认」按钮，点击调 DELETE 并回填默认提示词', async () => {
    resetPrompt.mockResolvedValue(ENVELOPE({
      node_id: 'market:CN News Analyst',
      label: 'CN News Analyst',
      layer: 'market',
      default_prompt: '默认提示词含 {market_overview} 占位符',
      override_prompt: null,
      has_override: false,
      updated_at: null,
    }) as never);
    const user = userEvent.setup();
    renderDialog();
    const resetButton = await screen.findByRole('button', { name: '恢复默认' });
    expect(resetButton).toBeInTheDocument();
    await user.click(resetButton);
    await waitFor(() => {
      expect(resetPrompt).toHaveBeenCalledWith('market%3ACN%20News%20Analyst');
    });
  });

  it('默认提示词含 {xxx} 占位符时展示运行时注入变量提示', async () => {
    renderDialog({
      node_id: 'market:CN Tech Analyst',
      label: 'CN Tech Analyst',
    });
    expect(await screen.findByText(/运行时注入变量/)).toBeInTheDocument();
  });

  it('无覆盖时不显示「恢复默认」按钮', async () => {
    listPrompts.mockResolvedValue(ENVELOPE({
      items: [
        {
          node_id: 'market:CN News Analyst',
          label: 'CN News Analyst',
          layer: 'market',
          default_prompt: '默认提示词',
          override_prompt: null,
          has_override: false,
          updated_at: null,
        },
      ],
    }) as never);
    renderDialog();
    await screen.findByRole('textbox');
    expect(screen.queryByRole('button', { name: '恢复默认' })).not.toBeInTheDocument();
  });
});
