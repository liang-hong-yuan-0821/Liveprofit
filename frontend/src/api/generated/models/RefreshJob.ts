/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Resource } from './Resource';
export type RefreshJob = {
    id: string;
    resource: Resource;
    target_trade_date: string;
    status: RefreshJob.status;
    attempt: number;
    processed: number;
    total: number;
    created_at?: (string | null);
    started_at: (string | null);
    heartbeat_at: (string | null);
    finished_at?: (string | null);
    error_code: (string | null);
    error_summary: (string | null);
    result?: (Record<string, any> | null);
};
export namespace RefreshJob {
    export enum status {
        QUEUED = 'QUEUED',
        RUNNING = 'RUNNING',
        RETRY_WAIT = 'RETRY_WAIT',
        SUCCEEDED = 'SUCCEEDED',
        PARTIAL = 'PARTIAL',
        FAILED = 'FAILED',
        CANCELLED = 'CANCELLED',
    }
}

