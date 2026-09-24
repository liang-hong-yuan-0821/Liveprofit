/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DailyResearchRunSummary = {
    task_id: string;
    kind: DailyResearchRunSummary.kind;
    trigger: DailyResearchRunSummary.trigger;
    slot?: (string | null);
    status: DailyResearchRunSummary.status;
    report_status?: (string | null);
    target_trade_date?: (string | null);
    scheduled_at?: (string | null);
    news_cutoff_at?: (string | null);
    created_at: string;
    started_at?: (string | null);
    finished_at?: (string | null);
    updated_at: string;
    attempt_no: number;
    error_code?: (string | null);
    error_summary?: (string | null);
    conclusion_summary?: (string | null);
    has_report?: boolean;
};
export namespace DailyResearchRunSummary {
    export enum kind {
        NEWS = 'news',
        QUANT = 'quant',
    }
    export enum trigger {
        SCHEDULED = 'scheduled',
        MANUAL = 'manual',
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

