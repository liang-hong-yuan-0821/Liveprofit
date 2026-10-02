/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 原子更新名称与全部账户字段（plan 4.2.1：一次条件更新、成功仅 version+1）。
 */
export type PortfolioUpdateRequest = {
    name: string;
    total_assets: number;
    available_cash: number;
    risk_per_trade_pct: number;
    min_risk_reward_ratio: number;
    max_total_position_pct: number;
    max_single_stock_pct: number;
    max_sector_pct: number;
    max_portfolio_open_risk_pct: number;
    max_sector_open_risk_pct: number;
    max_daily_new_risk_pct: number;
    max_drawdown_pct: number;
    max_daily_loss_pct: number;
    net_asset_value?: (number | null);
    peak_net_asset_value?: (number | null);
    day_start_net_asset_value?: (number | null);
    risk_facts_as_of?: (string | null);
    risk_profile?: ('CONSERVATIVE' | 'BALANCED' | 'AGGRESSIVE' | null);
    expected_version: number;
};

