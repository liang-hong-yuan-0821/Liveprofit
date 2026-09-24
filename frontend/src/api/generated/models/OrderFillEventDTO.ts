/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OrderFillEventDTO = {
    id: string;
    order_id: string;
    portfolio_id: string;
    position_id: (string | null);
    event_type: OrderFillEventDTO.event_type;
    reverses_fill_id: (string | null);
    quantity: string;
    fill_price: string;
    fill_trade_date: string;
    source: string;
    idempotency_key: string;
    note: (string | null);
    created_at: string;
};
export namespace OrderFillEventDTO {
    export enum event_type {
        CONFIRM = 'CONFIRM',
        CORRECT = 'CORRECT',
        VOID = 'VOID',
    }
}

