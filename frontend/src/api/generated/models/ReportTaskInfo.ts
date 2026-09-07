/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReportTaskInfo = {
    task_id: string;
    task_type: ReportTaskInfo.task_type;
    ticker: (string | null);
    effective_trade_date: (string | null);
    duration_ms?: (number | null);
};
export namespace ReportTaskInfo {
    export enum task_type {
        SINGLE_STOCK = 'SINGLE_STOCK',
        MARKET_WIDE = 'MARKET_WIDE',
    }
}

