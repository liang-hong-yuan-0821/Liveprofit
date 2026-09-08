/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TaskCreatedData = {
    task_id: string;
    status: TaskCreatedData.status;
    requested_trade_date: (string | null);
    effective_trade_date: (string | null);
    date_correction: (string | null);
    /**
     * 服务端派生的 SSE 相对 URL
     */
    events_url: string;
    /**
     * 服务端派生的报告相对 URL
     */
    report_url: string;
    /**
     * 服务端派生的执行调用日志相对 URL
     */
    execution_logs_url: string;
    /**
     * 服务端派生的图拓扑相对 URL
     */
    graph_topology_url: string;
};
export namespace TaskCreatedData {
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

