/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TaskDTO = {
    id: string;
    task_type: TaskDTO.task_type;
    ticker: (string | null);
    requested_trade_date: (string | null);
    effective_trade_date: (string | null);
    date_correction: (string | null);
    selected_layers: Array<string>;
    status: TaskDTO.status;
    attempt_no: number;
    next_retry_at: (string | null);
    error_code: (string | null);
    error_summary: (string | null);
    created_at: string;
    updated_at: string;
    /**
     * 当前 attempt 为重跑时记录起点节点 id（仅展示，触发源为消息级参数）；普通执行 null
     */
    rerun_from_node_id: (string | null);
    /**
     * 恒等于 /api/v1/analysis-tasks/{id}/events
     */
    events_url: string;
    /**
     * 恒等于 /api/v1/analysis-tasks/{id}/report
     */
    report_url: string;
    /**
     * 恒等于 /api/v1/analysis-tasks/{id}/execution-logs
     */
    execution_logs_url: string;
    /**
     * 恒等于 /api/v1/analysis-tasks/{id}/graph-topology
     */
    graph_topology_url: string;
};
export namespace TaskDTO {
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

