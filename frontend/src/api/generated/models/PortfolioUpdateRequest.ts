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
    expected_version: number;
};

