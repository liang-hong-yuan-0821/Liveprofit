/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OrderStatusRequest = {
    status: OrderStatusRequest.status;
    expected_revision: number;
};
export namespace OrderStatusRequest {
    export enum status {
        EXECUTING = 'EXECUTING',
        REJECTED = 'REJECTED',
        CANCELLED = 'CANCELLED',
        RECONCILIATION_REQUIRED = 'RECONCILIATION_REQUIRED',
        SUPERSEDED = 'SUPERSEDED',
    }
}

