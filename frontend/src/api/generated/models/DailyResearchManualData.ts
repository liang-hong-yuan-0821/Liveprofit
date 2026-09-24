/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DailyResearchManualData = {
    task_id: string;
    kind: DailyResearchManualData.kind;
    status: DailyResearchManualData.status;
    trigger?: string;
    idempotent_replay?: boolean;
};
export namespace DailyResearchManualData {
    export enum kind {
        NEWS = 'news',
        QUANT = 'quant',
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

