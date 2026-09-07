/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TaskListItemDTO = {
    id: string;
    task_type: TaskListItemDTO.task_type;
    ticker: (string | null);
    effective_trade_date: (string | null);
    status: TaskListItemDTO.status;
    attempt_no: number;
    error_code: (string | null);
    error_summary: (string | null);
    created_at: string;
    updated_at: string;
};
export namespace TaskListItemDTO {
    export enum task_type {
        SINGLE_STOCK = 'SINGLE_STOCK',
        MARKET_WIDE = 'MARKET_WIDE',
    }
    export enum status {
        PENDING = 'PENDING',
        QUEUED = 'QUEUED',
        RUNNING = 'RUNNING',
        RETRYING = 'RETRYING',
        SUCCEEDED = 'SUCCEEDED',
        FAILED = 'FAILED',
        CANCELLED = 'CANCELLED',
        CANCEL_REQUESTED = 'CANCEL_REQUESTED',
    }
}

