/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SuggestedOrderDTO = {
    id: string;
    portfolio_id: string;
    position_id: (string | null);
    lifecycle_id: (string | null);
    intent_id: (string | null);
    source_signal_id: (number | null);
    market: string;
    symbol: string;
    industry_code: (string | null);
    side: SuggestedOrderDTO.side;
    quantity: string;
    filled_quantity: string;
    limit_price: string;
    stop_price: (string | null);
    reserved_cash: string;
    reserved_risk: string;
    reason_code: string;
    status: string;
    revision: number;
    earliest_execution_trade_date: (string | null);
    created_at: string;
    updated_at: string;
};
export namespace SuggestedOrderDTO {
    export enum side {
        BUY = 'BUY',
        SELL = 'SELL',
    }
}

