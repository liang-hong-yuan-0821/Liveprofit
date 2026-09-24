/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type QuantSignalRowDTO = {
    id: number;
    ts_code: string;
    signal_kind: string;
    attempt_no: number;
    action?: (string | null);
    score?: (number | null);
    reason?: (string | null);
    entry_price?: (number | null);
    stop_loss?: (number | null);
    take_profit?: (number | null);
    sell_ratio?: (number | null);
    order_status?: (string | null);
    shares?: (number | null);
    notional?: (number | null);
    order_cost_price?: (number | null);
    valuation_price?: (number | null);
    risk_bucket?: (Record<string, any> | null);
    signal_trade_date?: (string | null);
    signal_price_basis?: (string | null);
    execution_price_basis?: (string | null);
    adj_factor_version?: (string | null);
    execution_market?: (Record<string, any> | null);
    order_entry_price?: (number | null);
    order_stop_price?: (number | null);
    order_take_price?: (number | null);
    earliest_execution_trade_date?: (string | null);
    available_sell_quantity?: (number | null);
    estimated_fees?: (number | null);
    estimated_slippage?: (number | null);
    execution_policy_version?: (string | null);
    error_code?: (string | null);
};

