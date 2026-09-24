/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SuggestedOrderPreviewDTO = {
    symbol: string;
    action: (string | null);
    shares: (number | null);
    notional: (number | null);
    order_cost_price: (number | null);
    valuation_price: (number | null);
    stop_loss: (number | null);
    take_profit: (number | null);
    risk_bucket: (Record<string, any> | null);
    order_entry_price?: (number | null);
    order_stop_price?: (number | null);
    order_take_price?: (number | null);
    earliest_execution_trade_date?: (string | null);
    estimated_fees?: (number | null);
    estimated_slippage?: (number | null);
    execution_policy_version?: (string | null);
};

