/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ActiveTaskDTO = {
    task_id: string;
    task_type: ActiveTaskDTO.task_type;
    ticker: (string | null);
    effective_trade_date: (string | null);
    status: ActiveTaskDTO.status;
    attempt_no: number;
    updated_at: string;
    next_retry_at: (string | null);
};
export namespace ActiveTaskDTO {
    export enum task_type {
        SINGLE_STOCK = 'SINGLE_STOCK',
        MARKET_WIDE = 'MARKET_WIDE',
    }
    export enum status {
        PENDING = 'PENDING',
        QUEUED = 'QUEUED',
        RUNNING = 'RUNNING',
        RETRYING = 'RETRYING',
    }
}

