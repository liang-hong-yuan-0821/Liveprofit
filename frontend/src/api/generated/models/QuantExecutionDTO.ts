/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HoldingSignalPreviewDTO } from './HoldingSignalPreviewDTO';
import type { MatchingBuyPreviewDTO } from './MatchingBuyPreviewDTO';
import type { PortfolioSnapshotDTO } from './PortfolioSnapshotDTO';
import type { QuantExecutionSummaryDTO } from './QuantExecutionSummaryDTO';
import type { StrategyAuditDTO } from './StrategyAuditDTO';
import type { SuggestedOrderPreviewDTO } from './SuggestedOrderPreviewDTO';
/**
 * decision["quant_execution"] 的无源码投影（旧报告为 null，不渲染面板）。
 */
export type QuantExecutionDTO = {
    strategy: StrategyAuditDTO;
    portfolio_snapshot: PortfolioSnapshotDTO;
    matching_buy_preview: Array<MatchingBuyPreviewDTO>;
    holding_signal_preview: Array<HoldingSignalPreviewDTO>;
    suggested_order_preview: Array<SuggestedOrderPreviewDTO>;
    summary: QuantExecutionSummaryDTO;
    warnings: Array<string>;
    valued_at?: (string | null);
};

