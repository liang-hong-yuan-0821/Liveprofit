/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 六种业务帧 data 的公共字段 + 事件专属可选字段。
 */
export type BusinessEventData = {
    task_id: string;
    attempt_no: number;
    occurred_at: string;
    schema_version?: string;
    sequence?: (number | null);
    phase?: ('market' | 'sector' | 'stock' | 'decision' | 'queue' | 'finish' | null);
    message?: (string | null);
    worker_id?: (string | null);
    report_id?: (string | null);
    duration_ms?: (number | null);
    error_code?: (string | null);
};

