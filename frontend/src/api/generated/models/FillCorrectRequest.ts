/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FillCorrectRequest = {
    quantity: (number | string);
    fill_price: (number | string);
    fill_trade_date: string;
    idempotency_key: string;
    expected_revision: number;
    note?: (string | null);
};

