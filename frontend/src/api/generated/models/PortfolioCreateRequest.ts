/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PortfolioCreateRequest = {
    name: string;
    total_assets?: (number | null);
    available_cash?: (number | null);
    risk_per_trade_pct?: (number | null);
    min_risk_reward_ratio?: (number | null);
    max_total_position_pct?: (number | null);
    max_single_stock_pct?: (number | null);
    max_sector_pct?: (number | null);
    max_portfolio_open_risk_pct?: (number | null);
    max_sector_open_risk_pct?: (number | null);
    max_daily_new_risk_pct?: (number | null);
    max_drawdown_pct?: (number | null);
    max_daily_loss_pct?: (number | null);
    net_asset_value?: (number | null);
    peak_net_asset_value?: (number | null);
    day_start_net_asset_value?: (number | null);
    risk_facts_as_of?: (string | null);
};

