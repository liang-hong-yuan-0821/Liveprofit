/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FillConfirmRequest = {
    quantity: (number | string);
    fill_price: (number | string);
    fill_trade_date: string;
    idempotency_key: string;
    expected_revision: number;
    source?: FillConfirmRequest.source;
    note?: (string | null);
};
export namespace FillConfirmRequest {
    export enum source {
        MANUAL = 'MANUAL',
        BROKER = 'BROKER',
    }
}

