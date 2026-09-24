/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PositionLifecycleDTO = {
    id: string;
    portfolio_id: string;
    position_id: (string | null);
    market: string;
    symbol: string;
    strategy_version_id: string;
    lifecycle_policy_version_id: string;
    actual_shares: string;
    initial_fill_price: (string | null);
    initial_stop_price: (string | null);
    risk_capacity_shares: (string | null);
    target_exposure_pct: string;
    target_shares: string;
    phase: string;
    profit_take_price: (string | null);
    profit_target_reached: boolean;
    confirmation_completed: boolean;
    profit_trim_completed: boolean;
    arc_neckline_price: (string | null);
    active_stop_price: (string | null);
    high_water_mark: (string | null);
    trailing_phase: (string | null);
    expectation_status: (string | null);
    expectation_observed_days: (number | null);
    expectation_window_days: (number | null);
    last_processed_trade_date: (string | null);
    state_version: number;
    closed_at: (string | null);
    created_at: string;
    updated_at: string;
};

